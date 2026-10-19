#!/usr/bin/env python3
"""Kimi PreToolUse and Stop Hook bridge.

[Role]
Handles native Kimi Hook events (`pretool` and `stop`). `pretool` publishes
single-use nonce bindings for MindIE MCP tool calls; `stop` sends a thin
notification to `mindie_knowledge.loop.cli.capture_hook` for admitted sessions.

[Invariants]
- Slash entry is MCP `mindie_entry`, never `TurnStarted`.
- `stop` never exits 2, never blocks the host, and generates a local `event_id`
  (`identity_kind="notification"`), never fabricating a native turn ID.
- Inactive/off sessions skip quietly; lost handoffs record bounded diagnostics.

[Timeouts & Locks]
- Synchronous hook budget is bounded by `HOOK_TOTAL` (10.0s); no LLM calls or
  network sync occur inside `handle_pretool` or `handle_stop`.

[Side-Effect Policy]
- Writes nonce bind files (`pretool`), appends local capture notifications, and
  records static diagnostics on handoff failure. Always prints `{}` and exits 0.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from consent import configured_community_path
from identity import (
    is_mindie_mcp_tool,
    locate_main_wire,
    parse_hook,
    publish_nonce_bind,
    session_from_hook,
)
from paths import config_path

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
    return parse_hook(sys.stdin.buffer.read())


def _record_failure(stage, category):
    try:
        import diagnostic_support

        diagnostic_support.failure("stop", stage, category, reportable=False)
    except Exception:
        pass


def handle_pretool():
    try:
        event = _read_event()
        name = event.get("tool_name")
        if is_mindie_mcp_tool(name):
            tool_input = event.get("tool_input") or {}
            publish_nonce_bind(
                session_from_hook(event),
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
    from admission import gate

    admission = gate()
    try:
        view = admission.inspect(session)
    except Exception:
        return None, "admission-unreadable"
    status = view.get("status") if isinstance(view, dict) else None
    if status == "unavailable":
        return None, "admission-unreadable"
    if status != "active":
        return None, None
    try:
        lease = admission.check(session)
    except ValueError:
        return None, "admission-unreadable"
    if not isinstance(lease.get("token"), str) or not lease["token"]:
        return None, "admission-schema"
    return lease, None


def handle_stop():
    try:
        event = _read_event()
        if event.get("hook_event_name") not in {None, "Stop"} or event.get("stop_hook_active") not in {None, False}:
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
        if lease is None or not configured_community_path().is_file():
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
            event_id=uuid.uuid4().hex,
            harness="kimi",
        )
        if transcript is not None:
            normalized["transcript_path"] = transcript
        if isinstance(cwd, str) and cwd:
            normalized["cwd"] = cwd[:4096]
        from mindie_knowledge.loop.cli import capture_hook

        try:
            from knowledge_service import prepare_service_env

            prepare_service_env()
            result = capture_hook(str(engine_path()), normalized)
        except Exception:
            _record_failure("handoff", "internal")
            _print({})
            return 0
        stage = result.get("stage") if isinstance(result, dict) else None
        if not isinstance(stage, str):
            _record_failure("handoff", "internal")
        elif stage in {"unavailable", "rejected"}:
            cause = result.get("cause") or result.get("reason")
            _record_failure(stage, cause if cause in SAFE_CAUSES else "internal")
    except Exception:
        _record_failure("hook", "internal")
    _print({})
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        raise SystemExit("bridge operation required")
    if argv[0] == "pretool":
        raise SystemExit(handle_pretool())
    if argv[0] == "stop":
        raise SystemExit(handle_stop())
    _print({})
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
