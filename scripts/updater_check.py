#!/usr/bin/env python3
"""Bounded update-check orchestration for the MindIE Kimi updater.

[Role]
Runs one serialized check: resolve a formal Release, stage and switch a
candidate generation, then run knowledge feed sync and diagnostic retention
inside the reserved tail budget. `updater.py` re-exports `check` so existing
callers and monkeypatches resolve through the updater module.

[Invariants]
- `check.lock` serializes checks and the whole check shares one monotonic
  deadline; feed sync and maintenance always run after candidate work.
- A failed candidate records a bounded retry record; a deferred busy service
  preserves `current.json` and reports honestly.
- Feed-sync status is written separately from generation-switch status.

[Timeouts & Locks]
- `CHECK_BUDGET = 240.0s`, `FEED_BUDGET = 60.0s`; all child commands are
  bounded `bounded_run` calls and the non-blocking `check.lock` never waits.

[Side-Effect Policy]
- Writes `status.json`, `failed.json`, and feed-sync fields only; generation
  switching remains in the caller-bound updater transaction helpers.
"""

from __future__ import annotations

import json
import os
import time

try:
    from bounded import run as bounded_run
    from genstate import (
        OperationLock,
        check_lock,
        failed_path,
        read_current,
        read_status,
        update_dir,
        write_status,
    )
    from paths import load_adapter_config
except ImportError:
    pass

CHECK_BUDGET = 240.0
FEED_BUDGET = 60.0
DEFAULT_REMOTE = "https://github.com/mindie-agent/mindie-agent-kimi.git"

_FEED_OK = frozenset({"synced", "unchanged"})
_FEED_PENDING = frozenset({"busy", "deferred"})
_FEED_ERROR = frozenset({"unavailable", "invalid", "exhausted"})
_FEED_KNOWN = _FEED_OK | _FEED_PENDING | _FEED_ERROR

_HOST = None


def bind(host):
    """Bind to the re-exporting `updater` module for patchable lookups."""
    global _HOST
    _HOST = host


def _h(name):
    if _HOST is not None:
        try:
            return getattr(_HOST, name)
        except AttributeError:
            pass
    return globals()[name]


def _feed_error_summary(rows):
    parts = [
        f"{row['repository']}:{row['status']}:{str(row.get('detail') or row.get('cause') or '')[:80]}"
        for row in rows if row["status"] not in _FEED_OK
    ]
    return "; ".join(parts)[:240]


def fold_feed_results(output):
    text = (output or "").strip()
    if not text:
        raise ValueError("knowledge sync returned empty output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("knowledge sync returned non-JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("knowledge sync result is not a list")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("knowledge sync row is not an object")
        status_val = item.get("status")
        repo = item.get("repository")
        if not isinstance(status_val, str) or not status_val:
            raise ValueError("knowledge sync row missing status")
        if not isinstance(repo, str) or not repo:
            raise ValueError("knowledge sync row missing repository")
        if status_val not in _FEED_KNOWN:
            raise ValueError("knowledge sync unknown status: " + status_val)
        rows.append(item)
    good = sum(1 for row in rows if row["status"] in _FEED_OK)
    pending = sum(1 for row in rows if row["status"] in _FEED_PENDING)
    failed = sum(1 for row in rows if row["status"] in _FEED_ERROR)
    if not rows or good == len(rows):
        return "ok", rows, None
    summary = _feed_error_summary(rows)
    if good and (pending or failed):
        return "degraded", rows, summary
    if failed:
        return "sync_failed", rows, summary
    if pending:
        return "deferred", rows, summary
    return "sync_failed", rows, summary


def _check_once(adapter: dict, deadline: float, *, force, build, idle, install, lock_timeout) -> int:
    current = _h("read_current")(adapter)
    remote = os.environ.get("MINDIE_KIMI_UPDATE_REMOTE") or adapter.get("update_remote") or _h("DEFAULT_REMOTE")
    if _h("_resolve_is_waiting")(adapter, force):
        status = _h("read_status")(adapter)
        print(
            json.dumps({
                "result": "retry-waiting" if status.get("resolve_failure_class") in _h("_RETRYABLE") else status.get("result", "check-failed"),
                "error": status.get("error"),
                "failure_class": status.get("resolve_failure_class"),
                "next_retry_at": status.get("next_retry_at"),
            })
        )
        return 0
    try:
        selected = _h("resolve_release")(remote, adapter, current, deadline)
    except TimeoutError:
        raise
    except Exception as exc:
        recorded = _h("_note_resolve_failure")(adapter, exc, current.get("sha"))
        print(
            json.dumps({
                "result": recorded.get("result", "check-failed"),
                "error": recorded.get("error"),
                "failure_class": recorded.get("resolve_failure_class"),
                "next_retry_at": recorded.get("next_retry_at"),
                "diagnostic": recorded.get("diagnostic"),
            })
        )
        return 1
    _h("_clear_resolve_wait")(adapter)
    candidate = selected["candidate"]
    if candidate is None:
        result = "current" if selected["status"] == "up_to_date" else selected["status"]
        status = _h("_record")(
            adapter, current_sha=current.get("sha"), result=result, error=None,
            release_selection=selected,
        )
        print(json.dumps(status))
        return int(result == "no_compatible_release" or status["result"] == "degraded")
    release = candidate["metadata"]
    sha = release["source"]["sha"]
    failed = _h("read_json")(_h("failed_path")(adapter))
    if not force and isinstance(failed, dict) and failed.get("sha") == sha and current.get("sha") != sha:
        if _h("_failure_gate")(failed) == "wait":
            result_name = "action-required" if failed.get("failure_class") in _h("_ACTIONABLE") else "retry-waiting"
            _h("_record")(
                adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result=result_name, error=failed.get("error"),
                failure_class=failed.get("failure_class"),
                next_retry_at=failed.get("next_retry_at"),
            )
            print(
                json.dumps({
                    "result": result_name, "sha": sha,
                    "failure_class": failed.get("failure_class"),
                    "next_retry_at": failed.get("next_retry_at"),
                    "error": failed.get("error"),
                })
            )
            return 0
    if sha == current.get("sha"):
        _h("_clear_active_failure")(adapter, sha)
        status = _h("_record")(adapter, current_sha=sha, result="current", error=None)
        print(json.dumps(status))
        return int(status["result"] == "degraded")
    try:
        generation, python, gen_adapter = _h("stage_generation")(
            sha, remote, adapter, deadline, build=build, release=release,
        )
    except TimeoutError:
        raise
    except Exception as exc:
        record = _h("_remember_failure")(adapter, sha, exc)
        diagnostic = _h("_update_diagnostic")("stage_generation", exc)
        _h("_record")(
            adapter, current_sha=current.get("sha"), candidate_sha=sha,
            result="failed", error=record.get("error"),
            failure_class=record.get("failure_class"),
            next_retry_at=record.get("next_retry_at"), diagnostic=diagnostic,
        )
        print(
            json.dumps({
                "result": "failed", "error": record.get("error"),
                "failure_class": record.get("failure_class"),
                "next_retry_at": record.get("next_retry_at"),
                "diagnostic": diagnostic,
            })
        )
        return 1
    try:
        _h("switch")(adapter, current, generation, python, gen_adapter, sha, deadline, idle, install, lock_timeout)
    except _h("LockTimeout"):
        _h("_record")(adapter, current_sha=current.get("sha"), candidate_sha=sha, result="deferred-busy", error="operation lock stayed shared")
        print(json.dumps({"result": "deferred-busy", "sha": sha}))
        return 0
    except _h("Deferred") as exc:
        _h("_record")(adapter, current_sha=current.get("sha"), candidate_sha=sha, result="deferred", error=str(exc)[:500])
        print(json.dumps({"result": "deferred", "sha": sha, "reason": str(exc)[:300]}))
        return 0
    except TimeoutError:
        raise
    except Exception as exc:
        record = _h("_remember_failure")(adapter, sha, exc)
        diagnostic = _h("_update_diagnostic")("switch", exc)
        _h("_record")(
            adapter, current_sha=current.get("sha"), candidate_sha=sha,
            result="failed", error=record.get("error"),
            failure_class=record.get("failure_class"),
            next_retry_at=record.get("next_retry_at"), diagnostic=diagnostic,
        )
        print(
            json.dumps({
                "result": "failed", "error": record.get("error"),
                "failure_class": record.get("failure_class"),
                "next_retry_at": record.get("next_retry_at"),
                "diagnostic": diagnostic,
            })
        )
        return 1
    _h("_clear_active_failure")(adapter, sha)
    status = _h("_record")(
        adapter, current_sha=sha, result="switched", needs_host_reload=True, error=None,
        note=(
            "MCP/hook dispatch uses the new generation per call; "
            "existing task authorization is preserved. Native "
            "Skill/command/MCP/hook definitions refresh with the Kimi host."
        ),
    )
    print(json.dumps(status))
    return int(status["result"] == "degraded")


def check(
    config=None, *, force=False, build=None,
    idle=None, install=None, lock_timeout=30.0,
) -> int:
    adapter = config if isinstance(config, dict) else _h("load_adapter_config")()
    _h("update_dir")(adapter).mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + _h("CHECK_BUDGET")
    try:
        with _h("check_lock")(adapter):
            code = _h("_check_once")(
                adapter, deadline, force=force, build=build or _h("build_runtime"),
                idle=idle or _h("stop_if_idle"), install=install or _h("native_install"),
                lock_timeout=lock_timeout,
            )
            _h("_feed_sync")(adapter, deadline)
            try:
                _h("_logging_maintenance")(adapter, deadline)
            except TimeoutError:
                raise
            except Exception as exc:
                _h("_update_diagnostic")("logging_maintenance", exc)
            return code
    except _h("LockTimeout"):
        print(json.dumps({"result": "check-already-running"}))
        return 0


def _logging_maintenance(adapter, deadline):
    status = _h("read_status")(adapter)
    remaining = deadline - time.monotonic()
    if remaining <= 0.1:
        status["logging_maintenance"] = {"status": "skipped_budget"}
        _h("write_status")(status, adapter)
        return
    try:
        with _h("OperationLock")(adapter).shared(timeout=min(0.5, remaining)):
            current = _h("read_current")(adapter)
        available = min(75, deadline - time.monotonic())
        if available <= 0:
            status["logging_maintenance"] = {"status": "skipped_budget"}
            _h("write_status")(status, adapter)
            return
        output = _h("bounded_run")(
            [
                current["python"], "-m", "mindie_diagnostics.cli",
                "reporting", "maintain", "--update-running", "--config",
                str(_h("diagnostic_support").reporting_config_path()),
                "--budget-seconds", str(max(0, available - 2)),
            ],
            "", timeout=available, max_output=65536, check=False,
        )
        payload = json.loads((output or "").strip() or "null")
        if not isinstance(payload, dict):
            raise ValueError("maintenance result is not an object")
        status["logging_maintenance"] = payload
    except _h("LockTimeout"):
        status["logging_maintenance"] = {"status": "deferred"}
    except TimeoutError:
        raise
    except Exception as exc:
        status["logging_maintenance"] = {"status": "unavailable", "type": type(exc).__name__}
    _h("write_status")(status, adapter)


def _feed_sync(adapter: dict, deadline: float) -> bool:
    status = _h("read_status")(adapter)
    left = deadline - time.monotonic()
    if left <= 0:
        status["feed_sync"] = "skipped: check budget exhausted"
        status["feed_error"] = "skipped: check budget exhausted"
        status.pop("feed_results", None)
        _h("write_status")(status, adapter)
        return False
    try:
        with _h("OperationLock")(adapter).shared(timeout=min(5, left)):
            current = _h("read_current")(adapter)
            engine = _h("_generation_engine")(current)
            git_bin = adapter.get("git_bin")
            env = dict(os.environ, MINDIE_GIT_BIN=git_bin) if isinstance(git_bin, str) and git_bin else None
            output = _h("bounded_run")(
                [current["python"], "-m", "mindie_knowledge.loop.cli", "sync", "--config", engine],
                "", timeout=min(_h("FEED_BUDGET"), deadline - time.monotonic()), env=env,
            )
        aggregate, rows, summary = _h("fold_feed_results")(output)
        status["feed_sync"] = aggregate
        status["feed_results"] = rows
        if summary:
            status["feed_error"] = summary
        else:
            status.pop("feed_error", None)
        _h("write_status")(status, adapter)
        return aggregate == "ok"
    except TimeoutError:
        raise
    except Exception as exc:
        status["feed_sync"] = "sync_failed"
        status["feed_error"] = str(exc)[:240]
        status.pop("feed_results", None)
        _h("write_status")(status, adapter)
        return False
