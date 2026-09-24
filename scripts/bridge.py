#!/usr/bin/env python3
"""Kimi plugin hooks only. Slash entry is MCP mindie_entry, not TurnStarted.

Stop never exits 2 and never continues the business model. The host omits
the native turn id from Stop, and no asynchronous record can prove it, so
Stop is a thin NOTIFICATION: real session_id, the current lease token, one
local event_id for this invocation (explicitly not a native turn id) and
the validated transcript reference. Cursor dedup and the single wake live
in the shared core facade `capture_hook`; the adapter does not read
transcript contents or copy the engine. Genuine off/inactive/recursive
skips stay quiet; failures that lose an otherwise real handoff are recorded
through the existing bounded diagnostics, never silently.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import (
    is_mindie_mcp_tool,
    locate_main_wire,
    parse_hook,
    publish_nonce_bind,
    session_from_hook,
)
from paths import community_config_path, config_path

MAX_HOOK_BYTES = 128 * 1024
SAFE_CAUSES = {
    "missing-identity",
    "admission-unreadable",
    "store-not-ready",
    "schema-not-ready",
    "store-locked",
    "budget-exhausted",
    "service-unavailable",
    "worker-unavailable",
    "wake-failed",
    "internal",
}


def _print(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _read_event():
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    return parse_hook(raw)


def _record_failure(stage, category):
    """One bounded static diagnostic. Never raises, never carries content."""
    try:
        import diagnostic_support

        diagnostic_support.failure("stop", stage, category, reportable=False)
    except Exception:
        pass


def handle_pretool():
    try:
        event = _read_event()
        name = event.get("tool_name")
        if not is_mindie_mcp_tool(name):
            _print({})
            return 0
        tool_input = event.get("tool_input") or {}
        session = session_from_hook(event)
        publish_nonce_bind(
            session,
            tool_input.get("request_nonce"),
            name,
            event.get("tool_call_id"),
            tool_input,
        )
    except Exception:
        pass
    _print({})
    return 0


def _authorized_lease(session):
    """(lease, category). lease with category None is authorized. lease=None
    with category None is genuinely inactive/missing/off: quiet. A set
    category is a static, honest failure state (never reported as off)."""
    from admission import gate

    admission = gate()
    try:
        view = admission.inspect(session)
    except Exception:
        return None, "admission-unreadable"
    status = view.get("status") if isinstance(view, dict) else None
    if status == "unavailable":
        return None, "admission-unreadable"
    if status == "paused":
        # An explicit circuit state, not inactivity and not contribution-off.
        return None, "admission-paused"
    if status != "active":
        return None, None
    try:
        lease = admission.check(session)
    except ValueError:
        # inspect saw an active lease but the token read failed. Fail closed
        # and say so; the core repeats the authoritative check anyway.
        return None, "admission-unreadable"
    if not isinstance(lease.get("token"), str) or not lease["token"]:
        return None, "admission-schema"
    return lease, None


def handle_stop():
    try:
        event = _read_event()
        if event.get("hook_event_name") not in {None, "Stop"}:
            _print({})
            return 0
        if event.get("stop_hook_active") not in {None, False}:
            _print({})
            return 0
        session = session_from_hook(event)
        if not config_path().is_file():
            _print({})
            return 0
        import sharing as sharing_mod
        from paths import engine_config_path as engine_path

        lease, unavailable = _authorized_lease(session)
        if unavailable is not None:
            _record_failure("admission", unavailable)
            _print({})
            return 0
        if lease is None:
            _print({})
            return 0
        if not community_config_path().is_file():
            _print({})
            return 0
        settings = sharing_mod.load()
        if settings.error is not None or not settings.schema_ok:
            _record_failure("sharing", "configuration")
            _print({})
            return 0
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else lease.get("project_root")
        if not sharing_mod.capture_allowed(lease, cwd):
            _print({})
            return 0
        try:
            transcript = str(locate_main_wire(session))
        except ValueError:
            transcript = None
        normalized = dict(
            hook_event_name="Stop",
            identity_kind="notification",
            session_id=session,
            mindie_activation=lease["token"],
            # One local notification id per native Stop invocation. This is
            # NOT a native turn id and must never be read from the wire.
            event_id=uuid.uuid4().hex,
            harness="kimi",
        )
        if transcript is not None:
            normalized["transcript_path"] = transcript
        if isinstance(cwd, str) and cwd:
            normalized["cwd"] = cwd[:4096]
        from mindie_knowledge.loop.cli import capture_hook

        try:
            result = capture_hook(str(engine_path()), normalized)
        except Exception:
            _record_failure("handoff", "internal")
            _print({})
            return 0
        stage = result.get("stage") if isinstance(result, dict) else None
        if not isinstance(stage, str):
            # Attempted dispatch is not acceptance: an older facade answers
            # None for an event it cannot take. Record the lost handoff.
            _record_failure("handoff", "internal")
        elif stage in {"unavailable", "rejected"}:
            cause = result.get("cause") or result.get("reason")
            if cause not in SAFE_CAUSES:
                cause = "internal"
            _record_failure(stage, cause)
    except Exception:
        _record_failure("hook", "internal")
    _print({})
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("bridge operation required")
    op = argv[0]
    if op == "pretool":
        raise SystemExit(handle_pretool())
    if op == "stop":
        raise SystemExit(handle_stop())
    # Leftover hook names (turn-started, command): never replay; fail open.
    _print({})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
