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
import math
import os
from pathlib import Path
import re
import sys
import uuid

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z")

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


def _read_event() -> dict:
    raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8-sig") if isinstance(raw, (bytes, bytearray)) else str(raw).lstrip("\ufeff")
    event = json.loads(text)
    if not isinstance(event, dict):
        raise ValueError("hook payload must be one JSON object")
    return event


def _require_session(event: dict) -> str:
    value = event.get("session_id")
    if not isinstance(value, str) or not _SESSION_RE.fullmatch(value):
        raise ValueError("native session identity is missing or invalid")
    return value


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
        from identity import is_mindie_mcp_tool, publish_nonce_bind, session_from_hook

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
    except TimeoutError:
        raise
    except Exception:
        pass
    _print({})
    return 0


def _stdlib_config_path() -> Path:
    if override := os.environ.get("MINDIE_KIMI_CONFIG"):
        return Path(override).expanduser().absolute()
    if xdg := os.environ.get("XDG_CONFIG_HOME"):
        return (Path(xdg).expanduser() / "mindie-agent" / "kimi.json").absolute()
    if explicit_home := os.environ.get("MINDIE_CONFIG_HOME"):
        return (Path(explicit_home).expanduser() / "kimi.json").absolute()
    return (Path.home() / ".config" / "mindie-agent" / "kimi.json").absolute()


def _stdlib_within_roots(candidate: str, roots: list) -> bool:
    try:
        target = os.path.realpath(candidate)
    except TimeoutError:
        raise
    except OSError:
        return False
    if os.name == "nt":
        target = os.path.normcase(target)
    for item in roots:
        if not isinstance(item, str) or not os.path.isabs(item):
            continue
        try:
            base = os.path.realpath(item)
            if os.name == "nt":
                base = os.path.normcase(base)
            if os.path.commonpath([base, target]) == base:
                return True
        except TimeoutError:
            raise
        except (OSError, ValueError):
            continue
    return False


def _fast_stop_skip(event: dict) -> tuple[bool, Path | None]:
    """Pure-stdlib fast pre-check before importing paths/sharing/identity/mindie_knowledge."""
    cfg_file = _stdlib_config_path()
    if not cfg_file.is_file():
        return True, None
    try:
        cfg = json.loads(cfg_file.read_bytes().decode("utf-8-sig"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False, None
    if not isinstance(cfg, dict):
        return False, None
    comm_val = cfg.get("community_config")
    if not isinstance(comm_val, str) or not os.path.isabs(comm_val):
        return False, None
    comm_file = Path(comm_val)
    if not comm_file.is_file():
        return True, comm_file
    try:
        raw = json.loads(comm_file.read_bytes().decode("utf-8-sig"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False, comm_file
    if (
        not isinstance(raw, dict)
        or raw.get("schema") != "mindie-community-config/1"
        or type(raw.get("enabled")) is not bool
    ):
        return False, comm_file
    idle = raw.get("idle_seconds")
    if idle is not None and (type(idle) is not int or not (30 <= idle <= 86400)):
        return False, comm_file
    roots = raw.get("project_roots", [])
    if not isinstance(roots, list) or not all(isinstance(r, str) and os.path.isabs(r) for r in roots):
        return False, comm_file
    if raw["enabled"] is False:
        return True, comm_file
    gen = raw.get("generation")
    enabled_at = raw.get("enabled_at")
    if not (
        isinstance(gen, str)
        and gen
        and type(enabled_at) in (int, float)
        and math.isfinite(enabled_at)
        and enabled_at > 0
    ):
        return False, comm_file
    consent_ref = raw.get("consent_config")
    if not isinstance(consent_ref, str) or not os.path.isabs(consent_ref):
        return False, comm_file
    cpath = Path(consent_ref)
    if not cpath.is_file():
        return True, comm_file
    try:
        cdata = json.loads(cpath.read_bytes().decode("utf-8-sig"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False, comm_file
    if not isinstance(cdata, dict) or cdata.get("schema") != "mindie-consent/1":
        return False, comm_file
    if cdata.get("choice") in {"disabled", "read-only", "later"}:
        return True, comm_file
    if cdata.get("choice") != "contribute":
        return False, comm_file
    if not roots:
        return True, comm_file
    for key in ("cwd", "workspace_root"):
        cand = event.get(key)
        if isinstance(cand, str) and os.path.isabs(cand):
            return (not _stdlib_within_roots(cand, roots)), comm_file
    return False, comm_file


def _event_workspace(event: dict, session: str) -> str | None:
    for key in ("cwd", "workspace_root"):
        candidate = event.get(key)
        if isinstance(candidate, str) and os.path.isabs(candidate):
            return candidate
    from identity import session_cwd

    try:
        resolved = session_cwd(session)
    except ValueError:
        return None
    return resolved if isinstance(resolved, str) and os.path.isabs(resolved) else None


def _ensure_stop_lease(session: str, cwd: str, settings):
    from admission import ensure_default_lease, gate, knowledge_lease
    from paths import admission_path

    resolved_cwd = str(Path(cwd).resolve())
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
                if existing.get("project_root") == resolved_cwd:
                    return existing, None
    if not settings.in_scope(resolved_cwd):
        return None, None
    try:
        lease = ensure_default_lease(session, project_root=resolved_cwd, not_before=0.0)
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
        session = _require_session(event)
        skip, comm_file = _fast_stop_skip(event)
        if skip:
            _print({})
            return 0
        from identity import locate_main_wire
        from paths import community_config_path, config_path, engine_config_path as engine_path, load_engine_config_at, profile_config_path
        import sharing as sharing_mod

        if not config_path().is_file():
            _print({})
            return 0
        if comm_file is None:
            try:
                comm_file = community_config_path()
            except FileNotFoundError:
                comm_file = profile_config_path().parent / "mindie-community.json"
            except TimeoutError:
                raise
            except (OSError, ValueError):
                _record_failure("sharing", "configuration")
                _print({})
                return 0
            if not comm_file.is_file():
                _print({})
                return 0

        settings = sharing_mod.load(comm_file)
        if settings.error is not None or not settings.schema_ok:
            _record_failure("sharing", "configuration")
            _print({})
            return 0
        if not settings.allows_capture():
            _print({})
            return 0
        cwd = _event_workspace(event, session)
        if cwd is None or not settings.in_scope(cwd):
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
        effective_cwd = str(Path(cwd).resolve())
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
        from mindie_knowledge.loop.cli import capture_hook

        cli_mod = sys.modules.get("mindie_knowledge.loop.cli")
        try:
            from knowledge_service import prepare_service_env

            prepare_service_env()
            eng = engine_path()
            if hasattr(cli_mod, "config_at"):
                eng_doc = load_engine_config_at(eng)
                if isinstance(eng_doc.get("root"), str) and isinstance(eng_doc.get("domain"), str):
                    from mindie_knowledge import state_layout

                    if not (state_layout.state_root(eng_doc["root"], eng_doc["domain"]) / state_layout.DATABASE).is_file():
                        from mindie_knowledge.loop.handoff import prepare_schema

                        prepare_schema(str(eng))
            result = capture_hook(str(eng), normalized)
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
