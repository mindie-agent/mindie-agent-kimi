#!/usr/bin/env python3
"""Kimi PreToolUse and Stop Hook bridge.

[Role]
Handles native Kimi Hook events (`pretool` and `stop`). `pretool` publishes
single-use bindings for MindIE MCP tool calls; `stop` sends a thin notification
to `mindie_knowledge.loop.cli.capture_hook` when community sharing is enabled.

[Invariants]
- `stop` checks community sharing consent and `project_roots` BEFORE opening
  `admission.sqlite3`; disabled or out-of-scope runs exit `{}` with zero SQLite I/O.
- `stop` never exits 2, never blocks the host, and generates a local `event_id`
  (`identity_kind="notification"`), never fabricating a native turn ID.
- Deactivated sessions skip quietly; lost handoffs record bounded diagnostics.

[Timeouts & Locks]
- Synchronous hook budget is bounded by `HOOK_TOTAL` (10.0s); no LLM calls or
  network sync occur inside `handle_pretool` or `handle_stop`.

[Side-Effect Policy]
- Writes one-shot bind rows (`pretool`), provisions default leases and appends
  local capture notifications on eligible `stop` events, and always prints `{}`.
"""

from __future__ import annotations

import json
import os
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
    publish_auto_bind,
    publish_nonce_bind,
    session_cwd,
    session_from_hook,
)
from paths import admission_path, community_config_path, config_path, profile_config_path

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
    except TimeoutError:
        raise
    except Exception:
        pass


def handle_pretool():
    try:
        event = _read_event()
        name = event.get("tool_name")
        if is_mindie_mcp_tool(name):
            tool_input = event.get("tool_input") or {}
            session = session_from_hook(event)
            if isinstance(tool_input, dict) and tool_input.get("request_nonce") is not None:
                publish_nonce_bind(
                    session,
                    tool_input.get("request_nonce"),
                    name,
                    event.get("tool_call_id"),
                    tool_input,
                )
            elif isinstance(tool_input, dict) and event.get("tool_call_id"):
                publish_auto_bind(
                    session,
                    name,
                    event.get("tool_call_id"),
                    tool_input,
                )
    except TimeoutError:
        raise
    except Exception:
        pass
    _print({})
    return 0


def _fast_community_path() -> Path | None:
    try:
        return community_config_path()
    except FileNotFoundError:
        return profile_config_path().parent / "mindie-community.json"
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None


def _fast_sharing_definitely_off(comm_file: Path) -> bool:
    """Pure-stdlib fast check returning True only for valid explicit disabled configs."""
    try:
        raw = json.loads(comm_file.read_bytes().decode("utf-8-sig"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict) or raw.get("schema") != "mindie-community-config/1":
        return False
    if raw.get("enabled") is False:
        return True
    consent_ref = raw.get("consent_config")
    if isinstance(consent_ref, str) and os.path.isabs(consent_ref):
        cpath = Path(consent_ref)
        if cpath.is_file():
            try:
                cdata = json.loads(cpath.read_bytes().decode("utf-8-sig"))
            except TimeoutError:
                raise
            except (OSError, ValueError):
                return False
            if (
                isinstance(cdata, dict)
                and cdata.get("schema") == "mindie-consent/1"
                and cdata.get("choice") in {"disabled", "read-only", "later"}
            ):
                return True
    return False


def _ensure_stop_lease(session: str, cwd: str | None, settings):
    from admission import ensure_default_lease, gate, knowledge_lease

    adm_file = admission_path()
    if adm_file.is_file():
        admission = gate()
        try:
            view = admission.inspect(session)
        except TimeoutError:
            raise
        except Exception:
            return None, "admission-unreadable"
        status = view.get("status") if isinstance(view, dict) else None
        if status == "unavailable":
            return None, "admission-unreadable"
        if status == "inactive":
            return None, None
        if status == "active":
            existing = knowledge_lease(session)
            if existing is not None:
                if not isinstance(existing.get("token"), str) or not existing["token"]:
                    return None, "admission-schema"
                if cwd is None or existing.get("project_root") == str(Path(cwd).resolve()):
                    return existing, None
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not settings.in_scope(cwd):
        return None, None
    try:
        lease = ensure_default_lease(session, project_root=cwd, not_before=0.0)
    except TimeoutError:
        raise
    except Exception:
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
        comm_file = _fast_community_path()
        if comm_file is None or not comm_file.is_file() or _fast_sharing_definitely_off(comm_file):
            _print({})
            return 0
        import sharing as sharing_mod
        from paths import engine_config_path as engine_path

        settings = sharing_mod.load(comm_file)
        if settings.error is not None or not settings.schema_ok:
            _record_failure("sharing", "configuration")
            _print({})
            return 0
        if not settings.allows_capture():
            _print({})
            return 0
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]) else None
        if cwd is None:
            try:
                cwd = session_cwd(session)
            except ValueError:
                cwd = None
        if cwd is not None and not settings.in_scope(cwd):
            _print({})
            return 0
        lease, unavailable = _ensure_stop_lease(session, cwd, settings)
        if unavailable is not None:
            _record_failure("admission", unavailable)
            _print({})
            return 0
        if lease is None:
            _print({})
            return 0
        effective_cwd = cwd or lease.get("project_root")
        if not sharing_mod.capture_allowed(lease, effective_cwd, config=comm_file):
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
        if isinstance(effective_cwd, str) and effective_cwd:
            normalized["cwd"] = effective_cwd[:4096]
        import mindie_knowledge.loop.cli as cli_mod

        try:
            from knowledge_service import prepare_service_env

            prepare_service_env()
            eng = engine_path()
            if hasattr(cli_mod, "config_at"):
                eng_doc = _read_json(Path(eng)) or {}
                if isinstance(eng_doc.get("root"), str) and isinstance(eng_doc.get("domain"), str):
                    from mindie_knowledge import state_layout

                    if not (state_layout.state_root(eng_doc["root"], eng_doc["domain"]) / state_layout.DATABASE).is_file():
                        from mindie_knowledge.loop.handoff import prepare_schema

                        prepare_schema(str(eng))
            result = cli_mod.capture_hook(str(eng), normalized)
        except TimeoutError:
            raise
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
    except TimeoutError:
        raise
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
