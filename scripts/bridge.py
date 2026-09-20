#!/usr/bin/env python3
"""Kimi plugin boundary: native hooks and exact slash-command activation.

UserPromptSubmit is not used: slash commands submit origin.kind=plugin_command
and that hook only runs for origin.kind==user. TurnStarted matcher
plugin_command plus this session's wire origin is the native entry.
Stop never exits 2, never starts the knowledge service, and checks claim bool.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import (
    is_mindie_mcp_tool,
    last_turn,
    locate_main_wire,
    parse_hook,
    plugin_command_from_wire,
    publish_nonce_bind,
    record_turn,
    session_from_hook,
)
from paths import (
    config_path,
    engine_config_path,
    load_adapter_config,
    load_engine_config,
)

MAX_HOOK_BYTES = 128 * 1024


def _print(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _read_event():
    raw = sys.stdin.buffer.read(MAX_HOOK_BYTES + 1)
    return parse_hook(raw)


def unconfigured_choices():
    return (
        "MindIE Agent is not configured on this machine.\n"
        "Three choices:\n"
        "1. Recommended: contribute public experience for the current project "
        "(/mindie-agent:sharing-enable with repository, absolute project root, "
        "and --visibility public).\n"
        "2. Read-only knowledge; no contribution (leave sharing off).\n"
        "3. Configure later (sharing stays off).\n"
        "Headless install leaves sharing off. Enabling requires an explicit "
        "choice; there is no automatic yes."
    )


def status_payload():
    result = dict(configured=False, sharing=dict(configured=False, enabled=False))
    path = config_path()
    if not path.is_file():
        result["hint"] = unconfigured_choices()
        result["recovery"] = [
            "Run scripts/setup.py --knowledge-python <venv-python> to write MindIE files.",
            "Install with Kimi native POST /api/v1/plugins source=local-path (see scripts/install_kimi_plugin.py).",
        ]
        return result
    config = load_adapter_config()
    engine = load_engine_config(config)
    result["configured"] = True
    result["domain"] = engine.get("domain")
    result["admission_path"] = engine.get("admission_path")
    try:
        import sharing as sharing_mod

        result["sharing"] = sharing_mod.public_status(config)
    except Exception as exc:
        result["sharing"] = dict(configured=False, enabled=False, error=str(exc)[:200])
    try:
        from admission import gate

        leases = gate(engine).leases()
        result["active_leases"] = len(leases)
    except Exception as exc:
        result["admission_error"] = str(exc)[:200]
    result["recovery"] = [
        "python -m mindie_knowledge.loop.cli contribution-inspect --config <engine.json> --batch <id>",
        "python -m mindie_knowledge.loop.cli contribution-reconcile --config <engine.json> --batch <id>",
        "python -m mindie_knowledge.loop.cli contribution-compact --config <engine.json> --batch <id>",
        "python -m mindie_knowledge.loop.cli sync --config <engine.json>",
    ]
    if not result["sharing"].get("enabled"):
        result["hint"] = (
            "Sharing is off: no Stop capture, transcript read, or background "
            "organizer. Knowledge retrieval still works after /mindie-agent:init."
        )
    return result


def activate_session(session, cwd):
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        cwd = os.getcwd()
    project_root = str(Path(cwd).resolve())
    from admission import activate
    from knowledge_service import ensure_service

    lease = activate(session, project_root=project_root, root_session=session)
    try:
        ensure_service(engine_config_path())
        lease = dict(lease, service="started")
    except Exception as exc:
        lease = dict(lease, service=f"not-started:{type(exc).__name__}")
    return dict(
        session=lease["session"],
        enabled=lease["enabled"],
        project_root=lease["project_root"],
        root_session=lease["root_session"],
        capture_schema=True,
        service=lease.get("service"),
        sharing=status_payload().get("sharing"),
    )


def sharing_enable_from_text(text):
    import sharing as sharing_mod

    try:
        argv = shlex.split(text or "")
    except ValueError:
        raise ValueError("could not parse sharing-enable arguments")
    repository = None
    roots = []
    branch = "main"
    visibility = None
    account = None
    fork = None
    args = list(argv)
    while args:
        item = args.pop(0)
        if item in {"--repository", "--community-repository"} and args:
            repository = args.pop(0)
        elif item in {"--project-root", "--community-project-root"} and args:
            roots.append(str(Path(args.pop(0)).expanduser().resolve()))
        elif item in {"--branch", "--community-branch"} and args:
            branch = args.pop(0)
        elif item in {"--visibility", "--community-visibility"} and args:
            visibility = args.pop(0)
        elif item in {"--account", "--community-account"} and args:
            account = args.pop(0)
        elif item in {"--fork", "--community-fork"} and args:
            fork = args.pop(0)
        elif item.startswith("--"):
            raise ValueError("unknown sharing flag: " + item)
    if not repository or not roots or visibility != "public":
        raise ValueError(
            "sharing-enable requires --repository owner/repo, "
            "--project-root /absolute/path (repeatable), and --visibility public"
        )
    return sharing_mod.write_enabled(
        repository=repository,
        project_roots=roots,
        branch=branch,
        visibility=visibility,
        account=account,
        fork=fork,
    )


def recover_payload(text=""):
    result = dict(status="ok", actions=[])
    engine = str(engine_config_path())
    python = load_adapter_config()["python"]
    batch = None
    try:
        argv = shlex.split(text) if text else []
        if "--batch-id" in argv:
            batch = argv[argv.index("--batch-id") + 1]
        elif "--batch" in argv:
            batch = argv[argv.index("--batch") + 1]
    except (ValueError, IndexError):
        batch = None
    from bounded import run

    def core(operation, extra=None):
        argv = [python, "-m", "mindie_knowledge.loop.cli", operation, "--config", engine]
        if extra:
            argv.extend(extra)
        return json.loads(run(argv, "", timeout=20))

    try:
        result["sharing_status"] = core("sharing-status")
        result["actions"].append("sharing-status")
    except Exception as exc:
        result["sharing_status_error"] = str(exc)[:200]
    if not batch:
        result["hint"] = "pass --batch <id> for contribution-inspect/reconcile/compact"
        return result
    extra = ["--batch", batch]
    for operation in (
        "contribution-inspect",
        "contribution-reconcile",
        "contribution-compact",
    ):
        try:
            result[operation] = core(operation, extra)
            result["actions"].append(operation)
        except Exception as exc:
            result[operation + "_error"] = str(exc)[:200]
    return result


def dispatch_command(session, cwd, command, arguments):
    if command in {"init", "status"}:
        payload = status_payload()
        if command == "init" and payload.get("configured"):
            payload["activation"] = activate_session(session, cwd)
        return payload
    if command == "deactivate":
        from admission import deactivate

        return dict(session=session, deactivated=bool(deactivate(session)))
    if command == "sharing-status":
        return status_payload()["sharing"]
    if command == "sharing-enable":
        return sharing_enable_from_text(arguments)
    if command == "sharing-disable":
        import sharing as sharing_mod

        return sharing_mod.write_disabled()
    if command == "recover":
        return recover_payload(arguments)
    raise ValueError("unknown MindIE native command: " + command)


def handle_command():
    """TurnStarted origin_kind=plugin_command: bind from this session's wire."""
    try:
        event = _read_event()
        if event.get("origin_kind") != "plugin_command":
            _print({})
            return 0
        session = session_from_hook(event)
        found = plugin_command_from_wire(session)
        if found is None:
            _print({})
            return 0
        try:
            payload = dispatch_command(
                session, event.get("cwd"), found["command"], found.get("arguments") or ""
            )
            _print(dict(command=found["command"], result=payload))
        except Exception as exc:
            _print(dict(command=found["command"], error=str(exc)[:300]))
    except Exception:
        _print({})
    return 0


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


def handle_turn_started():
    try:
        event = _read_event()
        session = session_from_hook(event)
        turn = event.get("turn_id")
        record_turn(session, turn)
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
        from admission import check, claim, finish
        from knowledge_service import existing_service
        from mindie_knowledge.loop.cli import rpc
        from paths import engine_config_path as engine_path

        lease = check(session)
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else lease.get("project_root")
        if not sharing_mod.capture_allowed(lease, cwd):
            _print({})
            return 0
        turn = event.get("turn_id")
        if turn is None:
            turn = last_turn(session)
        if turn is None:
            turn = "stop"
        try:
            transcript = str(locate_main_wire(session))
        except ValueError:
            transcript = None
        token = lease.get("token")
        if not isinstance(token, str):
            _print({})
            return 0
        if claim(session, "stop", str(turn)[:256], token=token) is not True:
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
            finish(session, token, True)
        except Exception:
            try:
                finish(session, token, False)
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
    if op == "command":
        raise SystemExit(handle_command())
    if op == "pretool":
        raise SystemExit(handle_pretool())
    if op == "turn-started":
        raise SystemExit(handle_turn_started())
    if op == "stop":
        raise SystemExit(handle_stop())
    if op == "status":
        _print(status_payload())
        return 0
    raise SystemExit("unsupported bridge operation")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
