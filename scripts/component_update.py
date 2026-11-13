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
from pathlib import Path
import time
import urllib.error

RETIRED_COMPONENTS = frozenset({"mindie-bench"})


class CatalogError(RuntimeError):
    """Expected catalog miss; static codes only, never raw feed text."""


def _catalog_error(code, *, category="temporary_network", status=None):
    error = CatalogError(code)
    error.code = code
    error.category = category
    if type(status) is int and 100 <= status <= 599:
        error.status = status
    return error


def typed_catalog_error(exc):
    from mindie_knowledge.release_http import ReleaseResponseTooLarge
    from mindie_knowledge.releases_io import ReleaseFetchError

    if isinstance(exc, ReleaseFetchError):
        return _catalog_error(
            exc.code,
            category="rate_limited" if exc.status == 429 else "temporary_network",
            status=exc.status,
        )
    if isinstance(exc, urllib.error.HTTPError):
        return _catalog_error(
            "release_http_error",
            category="rate_limited" if exc.code == 429 else "temporary_network",
            status=exc.code,
        )
    if isinstance(exc, json.JSONDecodeError):
        return _catalog_error("release_catalog_invalid")
    if isinstance(exc, (urllib.error.URLError, ConnectionError, ReleaseResponseTooLarge)):
        return _catalog_error("release_transport_unavailable")
    return exc


def _discover_catalog(*args, **kwargs):
    from mindie_knowledge.component_runtime import discover

    try:
        return discover(*args, **kwargs)
    except TimeoutError:
        raise
    except Exception as exc:
        raise typed_catalog_error(exc) from None


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


def prepare(adapter, source, python, deadline, *, force=False):
    import updater_check
    host = updater_check._HOST
    import mindie_knowledge.component_runtime as component_runtime
    from mindie_knowledge.component_bindings import required_components, rebind_commands
    from mindie_knowledge.component_runtime import build, source_baseline
    from mindie_knowledge.component_releases import Catalog
    from paths import profile_config_path
    current = host.read_current(adapter)
    profile_path = Path(adapter.get("profile_config") or profile_config_path())
    profile = (json.loads(profile_path.read_text())
               if adapter.get("profile_config") or profile_path.is_file() else adapter)
    if not isinstance(profile, dict):
        raise ValueError("Kimi profile configuration must be one object")
    baseline = source_baseline(source, active_overrides=True)
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
    needs_binding = bool(profile.get("component_commands")) and (
        rebind_commands(profile, python, selected["runtime"]) != profile["component_commands"])
    needs_build = installed != selected["runtime"] or retired_state["removed"]
    # Older controllers can stage only the bootstrap trio. Declared source
    # pins are a required floor, not proof of this interpreter's contents.
    if not needs_build and not needs_binding:
        return None
    generation, python = Path(source), Path(python)
    if needs_build:
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
    handoff = host.read_status(adapter).get("service_handoff") or {}
    status = handoff.get("status")
    if status in {"failed", "pending"}:
        return dict(
            status="failed" if status == "failed" else "degraded",
            generation=str(generation),
            python=str(python),
            service_handoff=handoff,
        )
    return dict(status="active", generation=str(generation), python=str(python))
