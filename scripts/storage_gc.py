#!/usr/bin/env python3
"""Bounded silent reclamation of historical MindIE Kimi runtime storage.

[Role]
Runs the updater's pre-build and post-switch storage maintenance: the shared
cross-harness legacy sweep, component-runtime pruning, and reclamation of
abandoned `mindie-kimi-summary-*` scratch profiles under the host temp root.

[Invariants]
- The shared storage layer is the only deletion authority; this module only
  supplies the current/rollback/bootstrap keep pins and aggregates its report.
- A live interpreter, lease, host wrapper or external plist keeps its
  generation. Unverified or incomplete process inspection only preserves more.
- Cleanup never changes the committed update result; every failure is returned
  as data for the caller's `cleanup_warning`, never raised as a stage fault.

[Timeouts & Locks]
- One absolute monotonic deadline bounds each phase. The sweep acquires no
  caller lock; shared storage returns deferred entries instead of waiting.

[Side-Effect Policy]
- Deletes only shared-storage classified disposable directories and abandoned
  legacy summary scratch roots. No config, host registration or current pointer
  is written here.
"""

from __future__ import annotations

import stat
import tempfile
import time
from pathlib import Path

SCHEMA = "mindie-kimi-storage-gc/1"
PRE_BUILD_CEILING = 5.0
POST_SWITCH_CEILING = 8.0
SUMMARY_GRACE_SECONDS = 3600.0
LEGACY_SUMMARY_PREFIX = "mindie-kimi-summary-"
_MAX_WARNINGS = 8


def _storage():
    from mindie_knowledge import storage

    return storage


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
    """Union the active generation, rollback receipt, bootstrap and runtime."""
    from genstate import launch_dir, update_dir

    keep = []

    def add(value):
        if isinstance(value, str) and value:
            keep.append(Path(value))

    if isinstance(current, dict):
        add(current.get("generation"))
        add(current.get("adapter_config"))
        add(current.get("python"))
        owner = _storage().owning_dir(str(current.get("python") or ""))
        if owner is not None:
            add(str(owner))
        sha = current.get("sha")
        if isinstance(sha, str) and sha:
            add(str(launch_dir(adapter) / sha))
        previous = _read_receipt_previous(adapter, current)
        if previous is not None:
            add(previous.get("generation"))
            add(previous.get("adapter_config"))
            add(previous.get("python"))
    state = update_dir(adapter).parent
    for name in ("bootstrap-venv", "bootstrap-package"):
        candidate = state / name
        if candidate.is_dir():
            keep.append(candidate)
    keep.append(update_dir(adapter) / "launch" / "bootstrap")
    return keep


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
    if time.monotonic() < limit:
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
    else:
        result["status"] = "partial"
        result["warnings"].append("budget exhausted")

    component_root = _component_root(adapter)
    if time.monotonic() < limit:
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
