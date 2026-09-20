#!/usr/bin/env python3
"""Kimi plugin hooks only. Slash entry is MCP mindie_entry, not TurnStarted.

Stop never exits 2, never starts the knowledge service, and checks claim
bool. Turn identity comes from the task's own wire; no turn store is kept,
so a default-off task creates no MindIE state.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import (
    current_turn_identity,
    is_mindie_mcp_tool,
    locate_main_wire,
    parse_hook,
    publish_nonce_bind,
    session_from_hook,
)
from paths import config_path

MAX_HOOK_BYTES = 128 * 1024


def _print(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _read_event():
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    return parse_hook(raw)


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
        from admission import gate
        from knowledge_service import existing_service
        from mindie_knowledge.loop.cli import rpc
        from paths import engine_config_path as engine_path

        admission = gate()
        try:
            lease = admission.check(session)
        except ValueError:
            _print({})
            return 0
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else lease.get("project_root")
        if not sharing_mod.capture_allowed(lease, cwd):
            _print({})
            return 0
        turn = event.get("turn_id")
        if turn is None:
            try:
                turn = current_turn_identity(session)
            except ValueError:
                _print({})
                return 0
        try:
            transcript = str(locate_main_wire(session))
        except ValueError:
            transcript = None
        token = lease.get("token")
        if not isinstance(token, str):
            _print({})
            return 0
        if admission.claim(session, "stop", str(turn)[:256], token=token) is not True:
            _print({})
            return 0
        try:
            connection = existing_service(engine_path())
            rpc(
                connection,
                "capture",
                dict(
                    session_id=session,
                    turn_id=str(turn),
                    transcript_path=transcript,
                    summary="",
                    cwd=cwd,
                    _session_id=session,
                    _activation=token,
                ),
                timeout=0.8,
            )
            admission.finish(session, token, True)
        except Exception:
            try:
                admission.finish(session, token, False)
            except Exception:
                pass
    except Exception:
        pass
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
