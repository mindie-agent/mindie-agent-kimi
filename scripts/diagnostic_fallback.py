"""Stdlib-only local diagnostics fallback for Kimi plugin scripts.

[Role]
Provides local JSONL fault recording (`record_failure`) and reporting consent
policy queries when `mindie_diagnostics` is not yet installed in the runtime.

[Invariants]
- Never records prompts, transcripts, commands, environment variables, or
  credentials; only bounded static labels (`component`, `operation`, `stage`,
  `category`) are persisted.
- Categories in `_NEVER_REPORTABLE` always force `reportable=False`.

[Timeouts & Locks]
- Uses an in-process `threading.Lock` and atomic `O_APPEND` writes bounded by
  `_MAX_EVENT_BYTES` (16 KiB) and `_MAX_FILE_BYTES` (1 MiB).

[Side-Effect Policy]
- Appends sanitized JSONL failure events under `<diagnostics_root>/events/<component>/`.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

_POLICY_SCHEMA = "mindie.diagnostics.reporting.v1"
_POLICY_PURPOSE = "tool_fault_reporting"
_MAX_EVENT_BYTES, _MAX_FILE_BYTES = 16384, 1024 * 1024
_LABEL_RE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,119}\Z")
_INCIDENT_RE = re.compile(r"[0-9a-f]{32}\Z")
_REVISION_RE = re.compile(r"[0-9a-f]{7,64}\Z")
_VERSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,79}\Z")
_REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}\Z")
_ERROR_TYPE_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,119}\Z")
_NEVER_REPORTABLE = frozenset({
    "caller", "cancelled", "business", "connectivity",
    "credentials", "permission", "disabled", "prerequisite",
})
_CONSENT_KEYS = frozenset({"config_file", "revision", "repository", "purpose"})
_lock = threading.Lock()
_process_pid, _process_uuid = None, None


class PolicyUnavailable(RuntimeError):
    """Raised when reporting policy state cannot be read."""


def _private_mkdir(path, *, parents=False, exist_ok=False) -> None:
    Path(path).mkdir(mode=0o700, parents=parents, exist_ok=exist_ok)


def policy_path(config=None) -> Path:
    if config is not None:
        return Path(config)
    if env := os.environ.get("MINDIE_DIAGNOSTICS_CONFIG"):
        return Path(env)
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent" / "diagnostics.json"


def default_root() -> Path:
    if env := os.environ.get("MINDIE_DIAGNOSTICS_ROOT"):
        return Path(env)
    if os.name == "nt" and not os.environ.get("XDG_STATE_HOME") and (local := os.environ.get("LOCALAPPDATA")):
        return Path(local) / "mindie" / "diagnostics"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "mindie" / "diagnostics"


def state_path(config=None) -> Path:
    return policy_path(config).with_suffix(".state")


def read_policy(config=None):
    try:
        path = policy_path(config)
        if not path.is_absolute() or not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != _POLICY_SCHEMA or data.get("purpose") != _POLICY_PURPOSE:
            return None
        decision, repo, rev, roots = data.get("decision"), data.get("repository"), data.get("revision"), data.get("roots")
        if decision not in ("enabled", "disabled") or not (isinstance(repo, str) and _REPO_RE.fullmatch(repo)):
            return None
        if not (isinstance(rev, str) and _INCIDENT_RE.fullmatch(rev)) or not isinstance(roots, list):
            return None
        if not all(isinstance(r, str) and Path(r).is_absolute() for r in roots):
            return None
        return {
            "schema": _POLICY_SCHEMA, "purpose": _POLICY_PURPOSE, "decision": decision,
            "repository": repo, "revision": rev, "roots": list(roots),
        }
    except Exception:
        return None


def current_consent(config=None):
    policy = read_policy(config)
    if not policy or policy.get("decision") != "enabled":
        return None
    return {
        "config_file": str(policy_path(config)), "revision": policy["revision"],
        "repository": policy["repository"], "purpose": policy["purpose"],
    }


def consent_status(config=None) -> dict:
    path = policy_path(config)
    if not path.exists():
        return {"enabled": False, "status": "not_configured", "config": str(path)}
    policy = read_policy(path)
    if policy is None:
        raise PolicyUnavailable("reporting policy is invalid or unreadable")
    enabled = policy["decision"] == "enabled"
    return {"enabled": enabled, "status": "enabled" if enabled else "disabled", "config": str(path)}


def consent_allowed(reference) -> bool:
    try:
        if not isinstance(reference, dict) or set(reference.keys()) != _CONSENT_KEYS:
            return False
        policy = read_policy(reference["config_file"])
        return bool(
            policy and policy.get("decision") == "enabled"
            and all(policy[k] == reference[k] for k in ("purpose", "repository", "revision"))
        )
    except Exception:
        return False


def scope_key(public_fingerprint, reference) -> str:
    try:
        if not isinstance(public_fingerprint, str) or not (0 < len(public_fingerprint) <= 256):
            return ""
        if not public_fingerprint.isascii() or any(ord(ch) < 33 or ord(ch) == 127 for ch in public_fingerprint):
            return ""
        if not isinstance(reference, dict) or set(reference.keys()) != _CONSENT_KEYS:
            return ""
        purpose, repo, rev = reference["purpose"], reference["repository"], reference["revision"]
        if purpose != _POLICY_PURPOSE or not (
            isinstance(repo, str) and _REPO_RE.fullmatch(repo) and isinstance(rev, str) and _INCIDENT_RE.fullmatch(rev)
        ):
            return ""
        canonical = json.dumps([purpose, repo, rev, public_fingerprint], ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    except Exception:
        return ""


def _label(value, limit) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and _LABEL_RE.fullmatch(value) is not None


def record_failure(
    component, operation, *, stage, category, exception=None, host=None,
    revision=None, version=None, elapsed_ms=None, exit_code=None,
    reportable=True, root=None, incident_id=None,
) -> dict:
    global _process_pid, _process_uuid
    try:
        if isinstance(incident_id, str) and _INCIDENT_RE.fullmatch(incident_id):
            return {"recorded": True, "incident_id": incident_id, "logging_failed": False}
        inv = {"recorded": False, "incident_id": None, "logging_failed": False, "error": "invalid_arguments"}
        if not (_label(component, 80) and _label(operation, 120) and _label(stage, 120) and _label(category, 120)) or not isinstance(reportable, bool):
            return inv
        attrs = {"stage": stage, "category": category}
        if exception is not None:
            etype = type(exception).__name__
            attrs["error_type"] = etype if isinstance(etype, str) and _ERROR_TYPE_RE.fullmatch(etype) else "Exception"
        if host is not None:
            if not _label(host, 120):
                return inv
            attrs["host"] = host
        if exit_code is not None:
            if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not (-2147483648 <= exit_code <= 2147483647):
                return inv
            attrs["exit_code"] = exit_code
        if elapsed_ms is not None and (
            isinstance(elapsed_ms, bool) or not isinstance(elapsed_ms, (int, float))
            or not math.isfinite(elapsed_ms) or not (0 <= elapsed_ms < 10**30)
        ):
            return inv
        if (revision is not None and not (isinstance(revision, str) and _REVISION_RE.fullmatch(revision))) or (
            version is not None and not (isinstance(version, str) and _VERSION_RE.fullmatch(version))
        ):
            return inv
        fresh, effective = uuid.uuid4().hex, (False if category in _NEVER_REPORTABLE else reportable)
        event = {
            "schema": 1, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(), "component": component,
            "severity": "ERROR", "event": "operation.end", "operation_id": fresh, "trace_id": fresh,
            "operation": operation, "status": "error", "attributes": attrs,
            "reportable": effective, "reporting": current_consent() if effective else None,
        }
        if elapsed_ms is not None:
            event["duration_ms"] = int(elapsed_ms) if isinstance(elapsed_ms, int) else elapsed_ms
        if revision is not None:
            event["package_revision"] = revision
        if version is not None:
            event["package_version"] = version
        with _lock:
            base = Path(default_root() if root is None else root)
            if not base.is_absolute():
                return {"recorded": False, "incident_id": None, "logging_failed": True, "error": "logging_failed"}
            leaf = base / "events" / component
            _private_mkdir(leaf, parents=True, exist_ok=True)
            pid = os.getpid()
            if _process_uuid is None or _process_pid != pid:
                _process_uuid, _process_pid = uuid.uuid4().hex, pid
            event["process_instance_id"] = _process_uuid
            payload = (json.dumps(event, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")
            target = leaf / f"{pid}-{_process_uuid}.jsonl"
            if len(payload) > _MAX_EVENT_BYTES or (target.exists() and target.stat().st_size + len(payload) > _MAX_FILE_BYTES):
                return {"recorded": False, "incident_id": None, "logging_failed": True, "error": "logging_failed"}
            fd = os.open(target, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                if os.write(fd, payload) == len(payload):
                    return {"recorded": True, "incident_id": fresh, "logging_failed": False}
            finally:
                os.close(fd)
    except Exception:
        pass
    return {"recorded": False, "incident_id": None, "logging_failed": True, "error": "logging_failed"}
