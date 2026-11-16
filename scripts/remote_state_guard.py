#!/usr/bin/env python3
"""Bounded protection for remote job and session state before reclamation.

[Role]
Scans a bounded set of JSON/JSONL records below a remote session or endpoint
state directory and decides whether reclamation may proceed. Also wraps the
shared `remote_dev.core.retention` session/endpoint pruning so a directory with
an unresolved persisted outcome is deferred instead of removed.

[Invariants]
- Only terminal job states are reclaimable. `unknown`/`uncertain`/`running`/
  `prepared`/`stopping`/`lost` and any unrecognized state are unresolved.
- `state`/`submission_state` are inspected recursively through nested dicts and
  lists up to `MAX_JSON_DEPTH` (12); deeper documents are unresolved. Only
  `authorization` subtrees are exempt from `state`, so `result.state` defers.
- A `remote-dev.job.v1` object without its own `state` is a pre-reply record:
  nested `authorization.job_id` never substitutes for it.
- `submission_state` is resolved only for terminal states plus
  `acknowledged`/`not_sent`/`not_executed`; a present `None`/non-string/empty defers.
- An incomplete walk (>400 entries), an oversized record (>512 KiB), an
  unreadable directory/file, or malformed JSON/JSONL is unresolved.
- Deletion always goes through `mindie_knowledge.storage.retire_directory`;
  this module never unlinks a tree itself.

[Timeouts & Locks]
- Every walk checks one absolute monotonic deadline. Each endpoint pass is
  capped by the shared retention budget (2s); at most 8 session roots and 16
  endpoint roots are inspected per sweep.

[Side-Effect Policy]
- Read-only scanning; the guarded wrappers only delegate to the shared
  retention/retirement authority. No host config, launchd job or network.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

TERMINAL_JOB_STATES = frozenset({
    "succeeded", "failed", "timeout", "cancelled", "completed", "canceled",
    "stopped", "killed", "exited", "aborted", "timed_out", "error", "absent",
    "done",
})
RESOLVED_SUBMISSION_STATES = frozenset({
    "acknowledged", "not_sent", "not_executed",
})
UNRESOLVED_SUBMISSION_STATES = frozenset({
    "uncertain", "unknown", "pending", "submitting",
})
JOB_SCHEMA = "remote-dev.job.v1"
AUTHORIZATION_KEY = "authorization"
MAX_FILES = 400
MAX_RECORD_BYTES = 512 * 1024
MAX_WALK_DEPTH = 8
MAX_JSON_DEPTH = 12
SESSION_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_SESSION_ROOTS = 8
MAX_ENDPOINT_ROOTS = 16


def _unresolved(result, reason):
    result["unresolved"] = True
    result["reason"] = reason
    return result


def _expired(deadline):
    return deadline is not None and time.monotonic() >= deadline


def _normalized(value):
    return value.strip().lower() if isinstance(value, str) else ""


def _record_unresolved(value, *, skip_state=False):
    """Return a static reason when one persisted record object is not terminal."""
    if not isinstance(value, dict):
        return None
    if value.get("schema_version") == JOB_SCHEMA and ("state" not in value
                                                      or value.get("state") is None):
        return "pre_reply_job"
    if "state" in value and not skip_state:
        if _normalized(value.get("state")) not in TERMINAL_JOB_STATES:
            return "non_terminal_state"
    if value.get("unknown") is True:
        return "unknown_outcome"
    if "submission_state" in value:
        # The key is present, so `None`/non-string/empty is an unconfirmed
        # persisted outcome, not an absent field.
        submission = _normalized(value.get("submission_state"))
        if submission not in RESOLVED_SUBMISSION_STATES \
                and submission not in TERMINAL_JOB_STATES:
            return "unresolved_submission"
    return None


def _scan_document(value):
    """Walk nested dictionaries/lists with a strict depth bound.

    Only an `authorization` subtree is exempt from the job `state` check:
    authorization metadata may carry its own `state` while `result.state`,
    `job.state` and any nested `submission_state` still defer reclamation.
    """
    pending = [(value, 0, False)]
    while pending:
        current, depth, skip_state = pending.pop()
        if depth > MAX_JSON_DEPTH:
            return "record_too_deep"
        if isinstance(current, dict):
            if (reason := _record_unresolved(current, skip_state=skip_state)) is not None:
                return reason
            for key, item in current.items():
                if isinstance(item, (dict, list)):
                    pending.append((item, depth + 1,
                                    skip_state or key == AUTHORIZATION_KEY))
        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (dict, list)):
                    pending.append((item, depth + 1, skip_state))
    return None


def unresolved_state(directory, *, deadline=None):
    """Return `{unresolved, reason, files, records}` for one state directory."""
    directory = Path(directory)
    result = {"unresolved": False, "reason": None, "files": 0, "records": 0}
    pending = [(directory, 0)]
    observations = 0
    while pending:
        if _expired(deadline):
            return _unresolved(result, "incomplete_walk")
        current, depth = pending.pop()
        try:
            entries = os.scandir(current)
        except TimeoutError:
            raise
        except OSError:
            return _unresolved(result, "unreadable_directory")
        try:
            with entries:
                for entry in entries:
                    if _expired(deadline):
                        return _unresolved(result, "incomplete_walk")
                    observations += 1
                    if observations > MAX_FILES:
                        return _unresolved(result, "incomplete_walk")
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            if depth >= MAX_WALK_DEPTH:
                                return _unresolved(result, "incomplete_walk")
                            pending.append((Path(entry.path), depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        info = entry.stat(follow_symlinks=False)
                    except TimeoutError:
                        raise
                    except OSError:
                        return _unresolved(result, "unreadable_entry")
                    result["files"] += 1
                    name = entry.name.casefold()
                    if not (name.endswith(".json") or name.endswith(".jsonl")):
                        continue
                    if info.st_size > MAX_RECORD_BYTES:
                        return _unresolved(result, "oversized_record")
                    try:
                        text = Path(entry.path).read_text(encoding="utf-8")
                    except TimeoutError:
                        raise
                    except (OSError, UnicodeError):
                        return _unresolved(result, "unreadable_record")
                    if name.endswith(".jsonl"):
                        documents = [line for line in text.splitlines() if line.strip()]
                    else:
                        documents = [text]
                    for document in documents:
                        try:
                            value = json.loads(document)
                        except (ValueError, RecursionError):
                            return _unresolved(result, "invalid_record")
                        result["records"] += 1
                        if (reason := _scan_document(value)) is not None:
                            return _unresolved(result, reason)
                    if _expired(deadline):
                        return _unresolved(result, "incomplete_walk")
        except TimeoutError:
            raise
        except OSError:
            return _unresolved(result, "unreadable_directory")
    return result


def has_unresolved_state(directory, *, deadline=None):
    """True when reclamation of `directory` could lose an unresolved outcome."""
    return bool(unresolved_state(directory, deadline=deadline)["unresolved"])


def _storage():
    from mindie_knowledge import storage

    return storage


def guarded_session_retention(root, *, now=None, max_age_seconds=SESSION_MAX_AGE_SECONDS,
                              deadline=None, dry_run=False):
    """Remove only dead, unlocked session runtime dirs whose records are terminal.

    The shared retention helper still decides age/receipt/lock/PID eligibility
    in a dry run. Each candidate is additionally scanned for unresolved JSON
    state immediately before, and again inside, the retirement transaction.
    """
    root = Path(root)
    report = {"status": "completed", "root": str(root), "scanned": 0,
              "removed": [], "deferred": [], "skipped": {}, "errors": [],
              "would_remove": []}
    if not root.is_dir():
        report["status"] = "absent"
        return report
    try:
        from remote_dev.core.retention import prune_stale_session_runtime_dirs
    except ImportError:
        report["status"] = "unavailable"
        return report
    try:
        dry = prune_stale_session_runtime_dirs(
            root, now=now, max_age_seconds=max_age_seconds, dry_run=True)
    except TimeoutError:
        raise
    except Exception as exc:
        report["status"] = "unavailable"
        report["errors"].append(type(exc).__name__)
        return report
    report["scanned"] = int(dry.get("scanned") or 0)
    report["errors"].extend(str(item)[:160] for item in (dry.get("errors") or [])[:4])
    for reason, count in (dry.get("skipped") or {}).items():
        if isinstance(count, int):
            report["skipped"][str(reason)] = report["skipped"].get(str(reason), 0) + count
    candidates = list(dry.get("would_remove") or [])

    def still_eligible(directory):
        # Age/lock/PID eligibility was proved by the dry run immediately above;
        # retirement itself creates lock files, so re-running the age check here
        # would always see a just-touched directory. Re-scan the records only.
        return not unresolved_state(directory, deadline=deadline)["unresolved"]

    for name in candidates:
        if _expired(deadline):
            report["status"] = "partial"
            report["skipped"]["budget"] = report["skipped"].get("budget", 0) + 1
            break
        directory = root / name
        guard = unresolved_state(directory, deadline=deadline)
        if guard["unresolved"]:
            report["deferred"].append(name)
            reason = guard["reason"] or "unresolved"
            report["skipped"][reason] = report["skipped"].get(reason, 0) + 1
            continue
        if dry_run:
            report["would_remove"].append(name)
            continue
        try:
            outcome = _storage().retire_directory(
                directory, deadline=deadline,
                recheck=lambda directory=directory: still_eligible(directory),
            )
        except TimeoutError:
            raise
        except Exception as exc:
            report["errors"].append(type(exc).__name__)
            continue
        if outcome.get("reclaimed"):
            report["removed"].append(name)
        elif outcome.get("status") == "deferred_in_use":
            report["deferred"].append(name)
        elif outcome.get("error"):
            report["errors"].append(str(outcome["error"])[:160])
    if report["deferred"] or report["errors"]:
        report["status"] = "partial"
    return report


def guarded_endpoint_retention(directory, *, force=False, deadline=None):
    """Prune one endpoint state dir only when every persisted outcome is terminal."""
    directory = Path(directory)
    if not directory.is_dir():
        return {"status": "absent", "base": str(directory)}
    guard = unresolved_state(directory, deadline=deadline)
    if guard["unresolved"]:
        return {"status": "deferred", "base": str(directory),
                "reason": guard["reason"], "deferred": True}
    try:
        from remote_dev.core.retention import prune_endpoint_state
    except ImportError:
        return {"status": "unavailable", "base": str(directory)}
    budget = 2.0 if deadline is None else max(
        0.1, min(2.0, deadline - time.monotonic()))
    try:
        outcome = prune_endpoint_state(
            directory, force=force, time_budget_seconds=budget)
    except TimeoutError:
        raise
    except Exception as exc:
        return {"status": "unavailable", "base": str(directory),
                "error": type(exc).__name__}
    return dict(outcome, status="completed")


__all__ = [
    "RESOLVED_SUBMISSION_STATES",
    "TERMINAL_JOB_STATES",
    "UNRESOLVED_SUBMISSION_STATES",
    "guarded_endpoint_retention",
    "guarded_session_retention",
    "has_unresolved_state",
    "unresolved_state",
]
