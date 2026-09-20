"""Slash-command operations invoked from MCP mindie_entry after nonce bind.

Unconfigured init/status/choose stay stdlib: no knowledge import, no service.
Sharing-enable destination is the native commandArgs, not model arguments.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from entry_state import consume_activation_id, first_use, set_first_use, three_choices
from identity import current_turn_origin, require_current_plugin_command, session_cwd
from paths import PLUGIN_ID, config_path, engine_config_path, load_adapter_config


def _configured() -> bool:
    return config_path().is_file()


def _parse_sharing(text):
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
    if not repository or not roots or visibility != "public" or not account:
        raise ValueError(
            "sharing-enable requires --repository owner/repo, "
            "--account name, --project-root /absolute/path, and --visibility public"
        )
    return dict(
        repository=repository,
        project_roots=roots,
        branch=branch,
        visibility=visibility,
        account=account,
        fork=fork,
    )


def consume_slash(session, command):
    found = require_current_plugin_command(session, command)
    if not _configured():
        return found, consume_activation_id(found["activation_id"])
    from admission import gate

    token = None
    try:
        lease = gate().active_lease(session)
        if lease and isinstance(lease.get("token"), str):
            token = lease["token"]
    except Exception:
        token = None
    if token is None:
        return found, consume_activation_id(found["activation_id"])
    return found, gate().claim(session, "plugin_command", found["activation_id"], token=token) is True


def status_payload(session=None):
    if not _configured():
        payload = three_choices()
        if payload["first_use"] in {"read-only", "later", "contribute"}:
            payload["repeat"] = True
            payload["choices"] = []
        return payload
    from sharing import public_status

    payload = dict(
        configured=True,
        sharing=public_status(),
        first_use=first_use(),
    )
    if session:
        from admission import gate

        lease = gate().active_lease(session)
        if lease is None:
            payload["this_session"] = dict(activated=False)
        else:
            payload["this_session"] = dict(
                activated=True,
                enabled=bool(lease.get("enabled")),
                failures=lease.get("failures", 0),
                project_root=lease.get("project_root"),
                paused=False,
            )
    stored = payload.get("first_use")
    if stored in {"read-only", "later", "contribute"}:
        payload["repeat"] = True
        payload["choices"] = []
    elif not payload["sharing"].get("enabled"):
        extra = three_choices()
        payload["choices"] = extra["choices"]
        payload["note"] = extra["note"]
        payload["setup"] = extra["setup"]
        payload["hint"] = (
            "Sharing is off: no Stop capture or organizer. "
            "Knowledge retrieval works after /mindie-agent:init."
        )
    return payload


def _project_root(session, cwd):
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return str(Path(cwd).resolve())
    try:
        return str(Path(session_cwd(session)).resolve())
    except ValueError:
        raise ValueError("native session cwd is required to activate; no caller task id is accepted")


def _init_choice_from_native(arguments):
    text = (arguments or "").strip()
    if not text:
        return None
    token = text.split()[0]
    if token in {"read-only", "later"}:
        return token
    return None


def op_init(session, cwd):
    found = require_current_plugin_command(session, "init")
    native_choice = _init_choice_from_native(found.get("arguments"))
    if native_choice is not None:
        if not consume_activation_id(found["activation_id"]) and not _configured():
            payload = status_payload(session)
            payload["already"] = True
            return payload
        if _configured():
            from admission import gate

            lease = gate().active_lease(session)
            token = lease.get("token") if lease else None
            if token:
                gate().claim(session, "plugin_command", found["activation_id"], token=token)
        set_first_use(native_choice)
        payload = status_payload(session)
        payload["first_use"] = native_choice
        payload["choices"] = []
        payload["repeat"] = True
        return payload
    if not _configured():
        if not consume_activation_id(found["activation_id"]):
            payload = status_payload(session)
            payload["already"] = True
            return payload
        return status_payload(session)
    from admission import activate, gate
    from knowledge_service import ensure_service

    root = _project_root(session, cwd)
    lease = activate(session, project_root=root, root_session=session)
    if gate().claim(session, "plugin_command", found["activation_id"], token=lease["token"]) is not True:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    try:
        ensure_service(engine_config_path())
        service = "started"
    except Exception as exc:
        service = f"not-started:{type(exc).__name__}"
    payload = status_payload(session)
    payload["activation"] = dict(
        session=lease["session"],
        enabled=lease["enabled"],
        failures=lease.get("failures", 0),
        project_root=lease["project_root"],
        paused=False,
        service=service,
    )
    return payload


def op_choose(session, choice):
    origin = current_turn_origin(session)
    kind = origin.get("kind")
    if kind == "plugin_command":
        if origin.get("pluginId") != PLUGIN_ID or origin.get("commandName") != "init":
            raise ValueError("choose is not valid on this native command")
    elif kind != "user":
        raise ValueError("choose requires an ordinary user reply or /mindie-agent:init")
    stored = set_first_use(choice)
    payload = status_payload(session)
    payload["first_use"] = stored
    payload["choices"] = []
    payload["repeat"] = True
    return payload


def op_status(session):
    consume_slash(session, "status")
    return status_payload(session)


def op_deactivate(session):
    consume_slash(session, "deactivate")
    if not _configured():
        return dict(session=session, deactivated=False, configured=False)
    from admission import deactivate

    return dict(session=session, deactivated=bool(deactivate(session)))


def op_sharing_enable(session, _model_arguments=""):
    found, first = consume_slash(session, "sharing-enable")
    if not first:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    if not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    import sharing as sharing_mod

    parsed = _parse_sharing(found.get("arguments") or "")
    result = sharing_mod.write_enabled(**parsed)
    set_first_use("contribute")
    return result


def op_sharing_disable(session):
    consume_slash(session, "sharing-disable")
    if not _configured():
        return dict(configured=False, enabled=False)
    import sharing as sharing_mod

    return sharing_mod.write_disabled()


def op_sharing_status(session):
    consume_slash(session, "sharing-status")
    if not _configured():
        return dict(configured=False, enabled=False)
    from sharing import public_status

    return public_status()


def op_recover(session, _model_arguments=""):
    found, _first = consume_slash(session, "recover")
    if not _configured():
        raise ValueError("MindIE is not configured")
    from bounded import run

    engine = str(engine_config_path())
    python = load_adapter_config()["python"]
    argv = shlex.split(found.get("arguments") or "")
    batch = None
    if "--batch" in argv:
        batch = argv[argv.index("--batch") + 1]
    elif "--batch-id" in argv:
        batch = argv[argv.index("--batch-id") + 1]
    result = dict(actions=[])
    if not batch:
        return dict(hint="pass --batch <id> on /mindie-agent:recover", **result)
    extra = ["--batch", batch]
    for operation in (
        "contribution-inspect",
        "contribution-reconcile",
        "contribution-compact",
    ):
        try:
            output = run(
                [python, "-m", "mindie_knowledge.loop.cli", operation, "--config", engine, *extra],
                "",
                timeout=20,
            )
            result[operation] = json.loads(output)
            result["actions"].append(operation)
        except Exception as exc:
            result[operation + "_error"] = str(exc)[:200]
    return result


def dispatch(session, cwd, op, arguments="", choice=None):
    if op == "init":
        return op_init(session, cwd)
    if op == "choose":
        if choice not in {"read-only", "later"}:
            raise ValueError(
                "choose requires choice=read-only or later; contribution uses sharing-enable"
            )
        return op_choose(session, choice)
    if op == "status":
        return op_status(session)
    if op == "deactivate":
        return op_deactivate(session)
    if op == "sharing-enable":
        return op_sharing_enable(session, arguments)
    if op == "sharing-disable":
        return op_sharing_disable(session)
    if op == "sharing-status":
        return op_sharing_status(session)
    if op == "recover":
        return op_recover(session, arguments)
    raise ValueError("unknown MindIE entry operation")
