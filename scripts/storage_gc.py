#!/usr/bin/env python3
"""Bounded silent reclamation of historical MindIE Kimi runtime storage.

[Role]
Runs the updater's pre-build and post-switch storage maintenance: the shared
cross-harness legacy sweep, component-runtime pruning, guarded remote
session/endpoint retention, and reclamation of abandoned
`mindie-kimi-summary-*` scratch profiles under the host temp root.

[Invariants]
- The shared storage layer is the only deletion authority; this module only
  supplies current/rollback/bootstrap/scheduler keep pins and aggregates data.
- A live interpreter, lease, host wrapper or replaced scheduler path keeps its
  generation. Unverified or unresolved state only preserves more.
- Cleanup never changes the committed update result; every failure is returned
  as data for the caller's `cleanup_warning`, never raised as a stage fault.

[Timeouts & Locks]
- One absolute monotonic deadline bounds each phase. Keep-path leases are held
  in one `ExitStack` for the whole sweep; no caller lock is acquired.

[Side-Effect Policy]
- Deletes only shared-retention-classified directories and abandoned legacy
  summary scratch roots. No config, host registration or current pointer is
  written here; endpoint pruning writes only its own cooldown marker.
"""

from __future__ import annotations

import os
import stat
import tempfile
import time
from contextlib import ExitStack
from pathlib import Path

from remote_state_guard import (
    MAX_ENDPOINT_ROOTS,
    MAX_SESSION_ROOTS,
    guarded_endpoint_retention,
    guarded_session_retention,
    has_unresolved_state,
    unresolved_state,
)

SCHEMA = "mindie-kimi-storage-gc/1"
PRE_BUILD_CEILING = 5.0
POST_SWITCH_CEILING = 8.0
SUMMARY_GRACE_SECONDS = 3600.0
LEGACY_SUMMARY_PREFIX = "mindie-kimi-summary-"
REMOTE_CEILING = 2.0
_MAX_WARNINGS = 8


def _storage():
    from mindie_knowledge import storage

    return storage


def _strip_verbatim(text, *, windows=None):
    """Drop a Windows `\\\\?\\` verbatim prefix (and restore `\\\\` for UNC)."""
    if (os.name == "nt" if windows is None else windows) and text.startswith("\\\\?\\"):
        text = text[4:]
        if text[:4].upper() == "UNC\\":
            text = "\\\\" + text[4:]
    return text


def _norm_path(value):
    """Host path identity; strips a Windows `\\\\?\\` verbatim prefix first."""
    return os.path.normcase(os.path.abspath(_strip_verbatim(str(value))))


def _remaining(deadline, ceiling):
    left = deadline - time.monotonic()
    if left <= 0:
        return 0.0
    return min(float(ceiling), left)


def _read_receipt_previous(adapter, current):
    sha = current.get("sha") if isinstance(current, dict) else None
    if not isinstance(sha, str) or not sha:
        return None
    try:
        from genstate import read_json, receipts_dir

        receipt = read_json(receipts_dir(adapter) / f"{sha}.json")
    except TimeoutError:
        raise
    except Exception:
        return None
    previous = receipt.get("previous") if isinstance(receipt, dict) else None
    return previous if isinstance(previous, dict) else None


def keep_paths(adapter, current):
    """Union the active, rollback, bootstrap and replaced scheduler runtimes."""
    from genstate import launch_dir, update_dir

    storage = _storage()
    keep = []
    seen = set()

    def add(value):
        if isinstance(value, str) and value:
            key = _norm_path(value)
            if key not in seen:
                seen.add(key)
                keep.append(Path(value))

    def add_reference(value):
        add(value)
        if not (isinstance(value, str) and value):
            return
        owner = storage.owning_dir(value)
        if owner is None:
            return
        add(str(owner))
        if owner.parent.name == "generations":
            add(str(owner.parent.parent / "launch" / owner.name))
        elif owner.parent.name == "launch":
            add(str(owner.parent.parent / "generations" / owner.name))

    if isinstance(current, dict):
        add_reference(current.get("generation"))
        add_reference(current.get("adapter_config"))
        add_reference(current.get("python"))
        sha = current.get("sha")
        if isinstance(sha, str) and sha:
            add(str(launch_dir(adapter) / sha))
        previous = _read_receipt_previous(adapter, current)
        if previous is not None:
            add_reference(previous.get("generation"))
            add_reference(previous.get("adapter_config"))
            add_reference(previous.get("python"))
    for pin in _scheduled_pins(adapter):
        add_reference(pin)
    state = update_dir(adapter).parent
    for name in ("bootstrap-venv", "bootstrap-package"):
        candidate = state / name
        if candidate.is_dir():
            add(str(candidate))
    add(str(update_dir(adapter) / "launch" / "bootstrap"))
    return keep


def _scheduled_pins(adapter):
    try:
        from host_pin_refresh import scheduled_pins

        return scheduled_pins(adapter)
    except TimeoutError:
        raise
    except Exception:
        return []


def _leased_directories(adapter, current):
    """Existing keep-path directories protected for the whole sweep.

    An external Git source checkout is already protected by the legacy sweep's
    ancestor rule; leasing it would create a lock file in the user's working
    tree. Only generated trees under `update/` may be leased there.
    """
    from genstate import update_dir

    owned = update_dir(adapter)
    directories, seen = [], set()
    for path in keep_paths(adapter, current):
        key = _norm_path(path)
        if key in seen:
            continue
        try:
            if not path.is_dir():
                continue
            if (path / ".git").exists() and not path.is_relative_to(owned):
                continue
            seen.add(key)
            directories.append(path)
        except TimeoutError:
            raise
        except OSError:
            continue
    return directories


def _merge(result, report, label):
    if not isinstance(report, dict):
        result["warnings"].append(label + ": no report")
        return
    for field in ("reclaimed_dirs", "deferred_dirs", "kept_dirs", "reclaimed_trash",
                  "reclaimed_files"):
        value = report.get(field)
        if type(value) is int:
            result[field] = result.get(field, 0) + value
    if report.get("partial") or report.get("status") not in (
            None, "completed", "absent", "skipped", "skipped_cooldown"):
        result["status"] = "partial"
    warnings = report.get("warnings")
    if isinstance(warnings, list):
        result["warnings"].extend(str(item)[:160] for item in warnings[:4])
    result["reports"][label] = {
        key: report.get(key)
        for key in ("status", "reclaimed_dirs", "deferred_dirs", "kept_dirs",
                    "reclaimed_trash", "partial", "inspection_complete")
        if key in report
    }


def _sweep_legacy_summary(temp_root, deadline):
    parent = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
    report = {"status": "completed", "reclaimed_dirs": 0, "deferred_dirs": 0,
              "warnings": [], "partial": False}
    try:
        entries = list(parent.iterdir())
    except TimeoutError:
        raise
    except OSError:
        return report
    candidates = []
    trash_prefix = ".trash-" + LEGACY_SUMMARY_PREFIX
    for entry in entries:
        if not (entry.name.startswith(LEGACY_SUMMARY_PREFIX)
                or entry.name.startswith(trash_prefix)):
            continue
        try:
            info = entry.lstat()
        except TimeoutError:
            raise
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            continue
        if getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            continue
        if time.time() - info.st_mtime < SUMMARY_GRACE_SECONDS:
            continue
        candidates.append(entry)
    if not candidates:
        return report
    storage = _storage()
    budget = max(0.1, deadline - time.monotonic())
    live, complete = storage.live_process_prefixes(timeout=min(2.0, budget))
    if not complete:
        report["status"] = "partial"
        report["partial"] = True
        report["warnings"].append("process inspection incomplete")
        return report

    def eligible(entry):
        try:
            info = entry.lstat()
        except TimeoutError:
            raise
        except OSError:
            return False
        if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            return False
        if time.time() - info.st_mtime < SUMMARY_GRACE_SECONDS:
            return False
        return not storage.is_path_used_by_live_process(entry, live)

    for entry in candidates:
        if time.monotonic() >= deadline:
            report["status"] = "partial"
            report["partial"] = True
            report["warnings"].append("budget exhausted")
            break
        if not eligible(entry):
            report["deferred_dirs"] += 1
            continue
        try:
            outcome = storage.retire_directory(
                entry, deadline=deadline, recheck=lambda entry=entry: eligible(entry)
            )
        except TimeoutError:
            raise
        except Exception as exc:
            report["warnings"].append("summary scratch: " + type(exc).__name__)
            continue
        if outcome.get("reclaimed"):
            report["reclaimed_dirs"] += 1
        elif outcome.get("status") == "deferred_in_use":
            report["deferred_dirs"] += 1
        if outcome.get("partial"):
            report["status"] = "partial"
            report["partial"] = True
    return report


def reclaim_remote_state(adapter, deadline):
    """Guard and reclaim abandoned remote session/endpoint state.

    The adapter keeps each Kimi session's `remote-dev` state under
    `<state_dir>/remote/<session>`. Session runtime trees are only reclaimed
    after the shared retention dry run and a bounded record scan prove every
    persisted outcome is terminal; endpoint pruning is skipped entirely when
    any record inside is unresolved.
    """
    from genstate import update_dir

    root = update_dir(adapter).parent / "remote"
    report = {"status": "absent", "root": str(root), "reclaimed_dirs": 0,
              "deferred_dirs": 0, "scanned_sessions": 0, "scanned_endpoints": 0,
              "warnings": [], "reports": {}}
    try:
        sessions = [entry for entry in root.iterdir()
                    if entry.is_dir() and not entry.is_symlink()]
    except TimeoutError:
        raise
    except OSError:
        return report
    report["status"] = "completed"
    for session in sorted(sessions)[:MAX_SESSION_ROOTS]:
        if time.monotonic() >= deadline:
            report["status"] = "partial"
            report["warnings"].append("remote sweep budget exhausted")
            break
        report["scanned_sessions"] += 1
        endpoints = session / "endpoints"
        try:
            endpoint_dirs = [entry for entry in endpoints.iterdir()
                             if entry.is_dir() and not entry.is_symlink()]
        except TimeoutError:
            raise
        except OSError:
            endpoint_dirs = []
        for endpoint in sorted(endpoint_dirs)[:MAX_ENDPOINT_ROOTS]:
            if time.monotonic() >= deadline:
                break
            report["scanned_endpoints"] += 1
            outcome = guarded_endpoint_retention(
                endpoint, force=True, deadline=min(deadline, time.monotonic() + 1.0))
            report["reports"][f"endpoint:{endpoint.name[:64]}"] = {
                "status": outcome.get("status"),
                **({"reason": outcome.get("reason")} if outcome.get("reason") else {}),
            }
        outcome = guarded_session_retention(
            session, deadline=min(deadline, time.monotonic() + 1.5))
        report["reclaimed_dirs"] += len(outcome.get("removed") or [])
        report["deferred_dirs"] += len(outcome.get("deferred") or [])
        if outcome.get("status") not in (None, "completed", "absent"):
            report["status"] = "partial"
        for warning in (outcome.get("errors") or [])[:2]:
            report["warnings"].append(str(warning)[:160])
        report["reports"][f"session:{session.name[:64]}"] = {
            key: outcome.get(key)
            for key in ("status", "scanned", "skipped")
            if key in outcome
        }
    if report["warnings"]:
        report["status"] = "partial"
    return report


def run(adapter, current, *, phase, budget_seconds=POST_SWITCH_CEILING,
        deadline=None, force=False, config_home=None, data_home=None,
        codex_home=None, temp_root=None):
    """Run one bounded sweep and return a structured, bounded report."""
    limit = time.monotonic() + max(0.0, float(budget_seconds))
    if deadline is not None:
        limit = min(limit, deadline)
    result = {
        "schema": SCHEMA, "phase": phase, "status": "completed",
        "reclaimed_dirs": 0, "deferred_dirs": 0, "reclaimed_trash": 0,
        "reclaimed_files": 0, "warnings": [], "reports": {},
    }
    storage = _storage()
    with ExitStack() as leases:
        leased = True
        for directory in _leased_directories(adapter, current):
            try:
                leases.enter_context(storage.acquire_runtime_lease(directory))
            except TimeoutError:
                raise
            except (OSError, RuntimeError):
                leased = False
                result["warnings"].append("lease: " + directory.name[:32])
        if leased and time.monotonic() < limit:
            kwargs = {"budget_seconds": max(0.1, limit - time.monotonic()),
                      "force": bool(force), "deadline": limit}
            if config_home is not None:
                kwargs["config_home"] = config_home
            if data_home is not None:
                kwargs["data_home"] = data_home
            if codex_home is not None:
                kwargs["codex_home"] = codex_home
            try:
                _merge(result, storage.sweep_legacy_installations(**kwargs), "legacy")
            except TimeoutError:
                result["status"] = "partial"
                result["warnings"].append("legacy sweep budget exhausted")
            except Exception as exc:
                result["warnings"].append("legacy sweep: " + type(exc).__name__)
        elif leased:
            result["status"] = "partial"
            result["warnings"].append("budget exhausted")

        component_root = _component_root(adapter)
        if leased and time.monotonic() < limit:
            try:
                _merge(result, storage.prune_component_runtimes(
                    component_root,
                    keep_paths=keep_paths(adapter, current),
                    budget_seconds=max(0.1, limit - time.monotonic()),
                    deadline=limit,
                ), "components")
            except TimeoutError:
                result["status"] = "partial"
                result["warnings"].append("component prune budget exhausted")
            except Exception as exc:
                result["warnings"].append("component prune: " + type(exc).__name__)

        if time.monotonic() < limit:
            try:
                _merge(result, _sweep_legacy_summary(temp_root, limit), "summary")
            except TimeoutError:
                result["status"] = "partial"
                result["warnings"].append("summary scratch budget exhausted")
            except Exception as exc:
                result["warnings"].append("summary scratch: " + type(exc).__name__)

        if time.monotonic() < limit:
            try:
                _merge(result, reclaim_remote_state(
                    adapter, min(limit, time.monotonic() + REMOTE_CEILING)), "remote")
            except TimeoutError:
                result["status"] = "partial"
                result["warnings"].append("remote state budget exhausted")
            except Exception as exc:
                result["warnings"].append("remote state: " + type(exc).__name__)

    if result["warnings"]:
        result["status"] = "partial"
        result["warnings"] = result["warnings"][:_MAX_WARNINGS]
    return result


def _component_root(adapter):
    from genstate import update_dir

    return update_dir(adapter) / "component-runtimes"


def pre_build(adapter, deadline, *, force=False, config_home=None, data_home=None,
              codex_home=None, temp_root=None):
    """Silent pre-build sweep; never blocks the candidate build."""
    from genstate import read_current

    budget = _remaining(deadline, PRE_BUILD_CEILING)
    if budget < 0.2:
        return {"schema": SCHEMA, "phase": "pre-build", "status": "skipped",
                "warnings": ["budget exhausted"], "reports": {}}
    try:
        current = read_current(adapter)
    except TimeoutError:
        raise
    except Exception:
        current = {}
    return run(adapter, current, phase="pre-build", budget_seconds=budget,
               deadline=deadline, force=force, config_home=config_home,
               data_home=data_home, codex_home=codex_home, temp_root=temp_root)


def post_switch(adapter, deadline, *, force=False, config_home=None,
                data_home=None, codex_home=None, temp_root=None):
    """Post-switch sweep whose failure is recorded, not raised."""
    from genstate import read_current

    budget = _remaining(deadline, POST_SWITCH_CEILING)
    if budget < 0.2:
        return {"schema": SCHEMA, "phase": "post-switch", "status": "skipped",
                "warnings": ["budget exhausted"], "reports": {}}
    try:
        current = read_current(adapter)
    except TimeoutError:
        raise
    except Exception:
        current = {}
    return run(adapter, current, phase="post-switch", budget_seconds=budget,
               deadline=deadline, force=force, config_home=config_home,
               data_home=data_home, codex_home=codex_home, temp_root=temp_root)
