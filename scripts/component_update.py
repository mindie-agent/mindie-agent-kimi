"""[Role] Advance component releases using Kimi's existing generation transaction.
[Invariants] Native plugin definitions are reinstalled only for plugin updates.
Component updates preserve the plugin SHA and all profile authority paths.
[Timeouts & Locks] Builder uses remaining enclosing budget minus updater.RESERVE
(rollback + feed + handoff) for catalog/inventory/build; exclusive operation
lock gates commit. Restore failure is never reported as components.status=active.
[Side-Effect Policy] Software release reads/builds and idle generation switch;
never authorizes knowledge, reads a corpus or changes contribution consent.
"""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import urllib.error

RETIRED_COMPONENTS = frozenset({"mindie-bench"})


class CatalogError(RuntimeError):
    """Expected catalog miss; static codes only, never raw feed text."""


def _catalog_error(code, *, category="temporary_network", status=None, retry_after=None):
    error = CatalogError(code)
    error.code = code
    error.category = category
    if type(status) is int and 100 <= status <= 599:
        error.status = status
    if retry_after is not None:
        error.retry_after = retry_after
    return error


def typed_catalog_error(exc):
    if isinstance(exc, TimeoutError):
        return exc
    from bounded import classify_transport_exception
    from mindie_knowledge.release_http import ReleaseResponseTooLarge
    from mindie_knowledge.releases_io import ReleaseFetchError
    from release_metadata import _catalog_category

    if isinstance(exc, ReleaseFetchError):
        return _catalog_error(
            exc.code,
            category=_catalog_category(exc.code, exc.status, getattr(exc, "category", None)),
            status=exc.status,
        )
    if isinstance(exc, urllib.error.HTTPError):
        classified = classify_transport_exception(exc)
        return _catalog_error(
            "release_http_error",
            category=classified[0] if classified else ("rate_limited" if exc.code == 429 else "temporary_network"),
            status=exc.code,
            retry_after=classified[1] if classified else None,
        )
    if isinstance(exc, ReleaseResponseTooLarge):
        return _catalog_error("release_transport_unavailable")
    if (classified := classify_transport_exception(exc)) is not None:
        category, retry_after = classified
        code = "release_certificate_error" if category == "certificate" else "release_transport_unavailable"
        return _catalog_error(code, category=category, retry_after=retry_after)
    return exc


def _discover_catalog(*args, **kwargs):
    from mindie_knowledge.component_runtime import discover

    try:
        return discover(*args, **kwargs)
    except TimeoutError:
        raise
    except Exception as exc:
        typed = typed_catalog_error(exc)
        if typed is exc:
            raise
        raise typed from None


@contextmanager
def _without_retired_inventory(runtime_module, retired_state: dict):
    original = getattr(runtime_module, "read_inventory", None)
    if original is None:
        yield
        return

    def filtered_inventory(python, *args, **kwargs):
        installed = dict(original(python, *args, **kwargs))
        removed = {
            name: installed.pop(name)
            for name in list(installed)
            if name in RETIRED_COMPONENTS
        }
        if removed:
            retired_state["removed"] = True
        return installed

    runtime_module.read_inventory = filtered_inventory
    try:
        yield
    finally:
        runtime_module.read_inventory = original


def _step_timeout(deadline, cap, reserve):
    left = deadline - time.monotonic() - reserve
    if left <= 0:
        raise TimeoutError("component update budget exhausted")
    return min(cap, left)


def _resolve(adapter, source, python, deadline, *, force=False):
    """Read the candidate floor and this interpreter's installed inventory."""
    import updater_check

    host = updater_check._HOST
    if host is None:
        import updater as host
    import mindie_knowledge.component_runtime as component_runtime
    from mindie_knowledge.component_bindings import required_components
    from mindie_knowledge.component_runtime import source_baseline
    from mindie_knowledge.component_releases import Catalog
    from paths import profile_config_path
    current = host.read_current(adapter)
    profile_path = Path(adapter.get("profile_config") or profile_config_path())
    profile = (json.loads(profile_path.read_text())
               if adapter.get("profile_config") or profile_path.is_file() else adapter)
    if not isinstance(profile, dict):
        raise ValueError("Kimi profile configuration must be one object")
    baseline = source_baseline(
        source,
        active_overrides=(Path(source) / "runtime" / "active-overrides.txt").is_file(),
    )
    required = {
        name: version
        for name, version in required_components(profile).items()
        if name not in RETIRED_COMPONENTS
    }
    retired_state = {"removed": False}
    with _without_retired_inventory(component_runtime, retired_state):
        selected = _discover_catalog(
            current["python"],
            baseline["runtime"],
            force=force,
            catalog=Catalog(budget=_step_timeout(deadline, 120, host.RESERVE)),
            required_components=required,
        )
        installed = (
            selected["installed"]
            if Path(python) == Path(current["python"])
            else component_runtime.read_inventory(
                python, timeout=_step_timeout(deadline, 15, host.RESERVE)
            )
        )
    if isinstance(selected.get("runtime"), dict):
        for name in RETIRED_COMPONENTS:
            if name in selected["runtime"]:
                selected["runtime"] = {
                    k: v for k, v in selected["runtime"].items() if k not in RETIRED_COMPONENTS
                }
                retired_state["removed"] = True
                break
    return dict(profile=profile, baseline=baseline, selected=selected,
                installed=installed, retired=retired_state["removed"])


def resolve_targets(adapter, source, python, deadline, *, force=False):
    """Return the merged component identities a candidate runtime must retain.

    Unlike `prepare`, this never reduces the answer to "the given interpreter
    already matches": it is the candidate floor (`max(installed inventory,
    declared active-overrides, remote formal releases)`). A caller building a
    fresh candidate venv uses it to pin the merged floor in one pass.
    """
    return dict(_resolve(adapter, source, python, deadline, force=force)["selected"]["runtime"])


def candidate_overrides(source, python, *, timeout=15):
    """Return the installed floor a fresh candidate venv must not downgrade.

    Call only after `prepare` proved this interpreter needs no component build:
    that proves the candidate's static pins are not newer than what is already
    installed, so the installed revisions are safe to prefer. Returns
    `{name: revision}` only when the candidate's `active-overrides.txt` alone
    would build a different floor; `None` means the static pins are exact.
    """
    from mindie_knowledge.component_runtime import read_inventory, source_baseline

    baseline = source_baseline(
        source,
        active_overrides=(Path(source) / "runtime" / "active-overrides.txt").is_file(),
    )["runtime"]
    installed = {
        name: row["revision"]
        for name, row in read_inventory(python, timeout=timeout).items()
    }
    merged = dict(baseline)
    merged.update(installed)
    return merged if merged != baseline else None


def candidate_components(adapter, python, deadline):
    """Return the component map installed in a freshly built candidate venv.

    `prepare()` returning `None` proves only that the *active* interpreter
    already meets the merged floor. The candidate venv was built with that same
    floor, so its managed module commands must still be rebound to the
    candidate interpreter; inheriting the active generation's
    `component_commands` leaves stale paths and defers the switch CAS.
    `None` means the profile declares no component commands; `{}` means only
    custom (unmanaged) commands, which need a live snapshot but no inventory.
    """
    import updater_check

    host = updater_check._HOST
    if host is None:
        import updater as host
    from mindie_knowledge.component_bindings import managed_commands

    profile = adapter
    profile_path = adapter.get("profile_config") if isinstance(adapter, dict) else None
    if isinstance(profile_path, str) and os.path.isabs(profile_path):
        try:
            value = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        except TimeoutError:
            raise
        except (OSError, ValueError):
            value = None
        if isinstance(value, dict):
            profile = value
    commands = profile.get("component_commands")
    if not isinstance(commands, dict) or not commands:
        return None
    if not managed_commands(profile):
        # Custom commands are not rebound, but the candidate still snapshots
        # the live profile so the switch CAS compares like with like.
        return {}
    from mindie_knowledge.component_runtime import read_inventory

    timeout = min(15, host._remaining(deadline, host.RESERVE))
    if timeout <= 0:
        raise TimeoutError("candidate component inventory budget exhausted")
    return read_inventory(python, timeout=timeout)


def prepare(adapter, source, python, deadline, *, force=False):
    import updater_check

    host = updater_check._HOST
    if host is None:
        import updater as host
    from mindie_knowledge.component_bindings import rebind_commands
    from mindie_knowledge.component_runtime import build

    resolved = _resolve(adapter, source, python, deadline, force=force)
    profile = resolved["profile"]
    selected = resolved["selected"]
    installed = resolved["installed"]
    needs_binding = bool(profile.get("component_commands")) and (
        rebind_commands(profile, python, selected["runtime"]) != profile["component_commands"])
    needs_build = installed != selected["runtime"] or resolved["retired"]
    # Older controllers can stage only the bootstrap trio. Declared source
    # pins are a required floor, not proof of this interpreter's contents.
    if not needs_build and not needs_binding:
        return None
    generation, python = Path(source), Path(python)
    if needs_build:
        baseline = resolved["baseline"]
        artifact = build(source, host.update_dir(adapter) / "component-runtimes", baseline,
                         selected["runtime"], python=python,
                         timeout=_step_timeout(deadline, 420, host.RESERVE))
        generation, python = Path(artifact["source"]), Path(artifact["python"])
    host.probe_runtime(python, deadline, generation)
    config = host.write_generation_configs(generation, python, adapter, components=selected["runtime"])
    return generation, python, config


def check(adapter, deadline, *, force, idle, install, lock_timeout):
    import updater_check
    host = updater_check._HOST
    current = host.read_current(adapter)
    prepared = prepare(adapter, Path(current["generation"]), current["python"], deadline, force=force)
    if prepared is None:
        return dict(status="up_to_date")
    generation, python, config = prepared
    # Retain the native payload for a later plugin rollback, without changing
    # its registered definitions during a component-only update.
    if current.get("sha"):
        host.build_host_package(generation, adapter, current["sha"], config_file=config)
    host.switch(adapter, current, generation, python, config, current.get("sha"), deadline,
                idle, install, lock_timeout, update_native=False)
    from storage_gc import post_switch

    try:
        cleanup = post_switch(adapter, deadline)
    except TimeoutError:
        cleanup = {"status": "partial", "phase": "post-switch", "warnings": ["budget exhausted"]}
    except Exception as exc:
        cleanup = {"status": "deferred", "phase": "post-switch", "warnings": [type(exc).__name__]}
    extra = {}
    if cleanup.get("status") not in {"completed", "skipped"} or cleanup.get("warnings"):
        extra["cleanup_warning"] = cleanup
    handoff = host.read_status(adapter).get("service_handoff") or {}
    status = handoff.get("status")
    if status in {"failed", "pending"}:
        return dict(
            status="failed" if status == "failed" else "degraded",
            generation=str(generation),
            python=str(python),
            service_handoff=handoff,
            **extra,
        )
    return dict(status="active", generation=str(generation), python=str(python), **extra)
