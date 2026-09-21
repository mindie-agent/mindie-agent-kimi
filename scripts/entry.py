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


def _diagnostics(session):
    """One bounded read of existing state; never activate or repair."""
    try:
        from mindie_knowledge.loop.diagnostics import snapshot

        return snapshot(str(engine_config_path()), session=session)
    except Exception as exc:
        return dict(
            status="unavailable",
            error=dict(stage="runtime_diagnostics", type=type(exc).__name__),
            hints=["Inspect the selected MindIE runtime. Native tools and independent SSH remain available; no recovery or retry was started."],
        )


def _knowledge_status_payload(session=None):
    if not _configured():
        payload = three_choices()
        if payload["first_use"] in {"read-only", "later", "contribute"}:
            payload["repeat"] = True
            payload["choices"] = []
        return payload
    from sharing import public_status

    try:
        sharing_view, choice = public_status(), first_use()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(
            configured=None,
            sharing=dict(enabled=None, status="unavailable"),
            error=dict(stage="local_settings", type=type(exc).__name__),
            config=str(config_path()),
            diagnostics=_diagnostics(session),
            hint="Inspect the existing adapter and community configuration. Native tools and independent SSH remain available; no setup or retry was started.",
        )
    payload = dict(configured=True, sharing=sharing_view, first_use=choice)
    diagnostics = _diagnostics(session)
    payload["diagnostics"] = diagnostics
    if session:
        admission = diagnostics.get("admission") or {}
        state = admission.get("status", "unavailable")
        payload["this_session"] = dict(
            status=state,
            activated=state in {"active", "paused"},
            enabled=state == "active" and admission.get("enabled") is True,
            failures=admission.get("failures"),
            project_root=admission.get("project_root"),
            paused=state == "paused",
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


def status_payload(session=None):
    """Read-only. Reporting is independent of knowledge setup and activation."""
    import diagnostic_support

    payload = dict(_knowledge_status_payload(session))
    payload["reporting"] = diagnostic_support.reporting_status()
    if payload.get("first_use") is None:
        payload["reporting_choice"] = diagnostic_support.reporting_hint()
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


def _apply_native_choice(payload, native_choice):
    if native_choice is None:
        return payload
    stored = set_first_use(native_choice)
    payload["first_use"] = stored
    payload["choices"] = []
    payload["repeat"] = True
    return payload


def _configured_init_activation(session, cwd, activation_id):
    """Explicit init activation for a configured task. Never auto-activates."""
    from admission import activate, gate
    from knowledge_service import ensure_service

    root = _project_root(session, cwd)
    try:
        lease = activate(session, project_root=root, root_session=session)
    except ValueError as exc:
        text = str(exc)
        if "paused" not in text.lower():
            raise
        payload = status_payload(session)
        payload["activation"] = dict(
            session=session,
            enabled=False,
            paused=True,
            hint=text[:300],
        )
        return payload
    if gate().claim(session, "plugin_command", activation_id, token=lease["token"]) is not True:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    try:
        ensure_service(engine_config_path())
        service = "started"
    except Exception as exc:
        service = f"not-started:{type(exc).__name__}"
    payload = status_payload(session)
    paused = bool(lease.get("paused"))
    payload["activation"] = dict(
        session=lease["session"],
        enabled=bool(lease.get("enabled")) and not paused,
        failures=lease.get("failures", 0),
        project_root=lease["project_root"],
        paused=paused,
        service=service,
    )
    return payload


def op_init(session, cwd):
    found = require_current_plugin_command(session, "init")
    native_choice = _init_choice_from_native(found.get("arguments"))
    if not _configured():
        if not consume_activation_id(found["activation_id"]):
            payload = status_payload(session)
            payload["already"] = True
            return payload
        if native_choice is None:
            return status_payload(session)
        return _apply_native_choice(status_payload(session), native_choice)
    payload = _configured_init_activation(session, cwd, found["activation_id"])
    return _apply_native_choice(payload, native_choice)


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
    # A bound task that explicitly enabled MindIE can diagnose a later failure,
    # including its paused lease, without another user slash command. This read
    # does not consume an attempt or grant/reactivate authorization.
    admitted = False
    if _configured():
        from admission import gate

        try:
            admitted = gate().inspect(session).get("status") in {"active", "paused"}
        except (OSError, ValueError, TypeError):
            pass
    if not admitted:
        require_current_plugin_command(session, "status")
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

    return dict(
        **public_status(), diagnostics=_diagnostics(session),
        hint="This task's batch IDs are in diagnostics.contributions; use /mindie-agent:recover --batch ID to inspect an existing contribution.",
    )


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
        return dict(
            hint="Use a batch_id from diagnostics.contributions with /mindie-agent:recover --batch ID; no recovery was started.",
            diagnostics=_diagnostics(session), **result,
        )
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


def op_reporting(session, command):
    """Native command only. Does not ensure the reporter or change knowledge."""
    found, first = consume_slash(session, command)
    del found
    import diagnostic_support

    if command == "reporting-status":
        return diagnostic_support.reporting_status()
    if not first:
        return dict(diagnostic_support.reporting_status(), already=True)
    if not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    python = load_adapter_config()["python"]
    return diagnostic_support.configure_reporting(command == "reporting-enable", python)


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
    if op in {"reporting-status", "reporting-enable", "reporting-disable"}:
        return op_reporting(session, op)
    raise ValueError("unknown MindIE entry operation")
