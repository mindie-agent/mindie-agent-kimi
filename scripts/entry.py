"""Slash-command operations invoked from MCP mindie_entry after nonce bind.

Unconfigured init/status/choose stay stdlib: no knowledge import, no service.
Sharing-enable destination is the native commandArgs, not model arguments.
"""

from __future__ import annotations

import json
import os
import shlex
import stat
from pathlib import Path

from entry_state import consume_activation_id, three_choices
from identity import (
    _entry_from_openings,
    current_entry_scan,
    require_current_entry,
    require_current_plugin_command,
    session_cwd,
)
from paths import config_path, engine_config_path, load_adapter_config


def _configured() -> bool:
    return config_path().is_file()


def _parse_sharing(text):
    """Native sharing-enable arguments. The project root may be repeated
    for backward compatibility but is validated against the native session
    cwd by the caller — the scope is always the current project."""
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
    if not repository or visibility != "public" or not account:
        raise ValueError(
            "sharing-enable requires --repository owner/repo, "
            "--account name, and --visibility public"
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


def _bounded_text(value, limit):
    if not isinstance(value, str) or not value:
        return None
    return value[:limit]


_META_LIMIT = 65536


def _read_regular_json(path):
    """Small JSON object from a regular file.

    Missing is "missing". FIFO, socket, device, and malformed files are
    "unavailable" and are not read as a blocking stream. A symlink is
    followed only when its target is a regular file.
    """
    try:
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unavailable", None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _META_LIMIT:
            return "unavailable", None
        blob = os.read(descriptor, _META_LIMIT + 1)
    except OSError:
        return "unavailable", None
    finally:
        os.close(descriptor)
    if len(blob) > _META_LIMIT:
        return "unavailable", None
    try:
        value = json.loads(blob.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, ValueError):
        return "unavailable", None
    if not isinstance(value, dict):
        return "unavailable", None
    return None, value


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _launcher_command(adapter, operation, current):
    """Absolute retained launcher command from an already-read current tuple."""
    try:
        from genstate import launch_dir

        if not isinstance(current, dict):
            return None
        sha = current.get("sha") if isinstance(current.get("sha"), str) and current.get("sha") else "bootstrap"
        python = current.get("python")
        if not isinstance(python, str) or not python:
            return None
        launcher = launch_dir(adapter) / sha / "mindie_launch.py"
        if not stat.S_ISREG(launcher.stat().st_mode):
            return None
        config_value = adapter.get("base_config") if isinstance(adapter.get("base_config"), str) else str(config_path())
        config_file = str(Path(config_value).expanduser().resolve())
        return shlex.join([python, str(launcher.resolve()), "--config", config_file, "updater", operation])
    except OSError:
        return None
    except Exception:
        return None


def _updater_view():
    """Read updater failure state. Does not check the network or mutate it."""
    try:
        from genstate import current_path, failed_path, status_path

        adapter = load_adapter_config()
        if not isinstance(adapter, dict):
            return {"status": "unavailable"}
        failed_state, failed = _read_regular_json(failed_path(adapter))
        status_state, status = _read_regular_json(status_path(adapter))
        current_state, current = _read_regular_json(current_path(adapter))
    except Exception:
        return {"status": "unavailable"}
    if "unavailable" in {failed_state, status_state, current_state}:
        return {"status": "unavailable"}
    failed = failed or {}
    status = status or {}
    current = current or {}
    if not isinstance(failed, dict):
        failed = {}
    if not isinstance(status, dict):
        status = {}
    klass = failed.get("failure_class") if isinstance(failed.get("failure_class"), str) else None
    if klass is None and isinstance(status.get("resolve_failure_class"), str):
        klass = status.get("resolve_failure_class")
    phase = failed.get("phase") if isinstance(failed.get("phase"), str) else None
    retryable = klass in {"temporary_network", "rate_limited"} and phase != "package-refresh"
    certificate = klass == "certificate"
    actionable = klass in {"authentication", "permission", "hook_trust", "certificate"}
    quarantined = klass in {"resolver", "bad_content"} or failed.get("quarantined") is True
    unknown = bool(failed.get("error")) and not retryable and not actionable and not quarantined
    nxt = _number(failed.get("next_retry_at"))
    if nxt is None:
        nxt = _number(status.get("next_retry_at"))
    active = bool(
        failed.get("error") or klass or failed.get("quarantined") or phase or nxt is not None
        or status.get("error") or status.get("resolve_wait")
        or status.get("result") in {
            "failed", "check-failed", "action-required",
            "suppressed-known-failed", "retry-waiting",
        }
    )
    if not active:
        if isinstance(failed.get("sha"), str) or failed.get("history") or failed.get("count"):
            return {
                "status": "recovered",
                "recovery": "recovered",
                "active_failure": False,
                "note": "no active updater failure; history remains in updater status",
            }
        return None
    view = {"active_failure": True}
    if isinstance(status.get("result"), str):
        view["result"] = status["result"][:80]
    if isinstance(failed.get("sha"), str):
        view["failed_sha"] = failed["sha"][:64]
    error = _bounded_text(failed.get("error"), 200) or _bounded_text(status.get("error"), 200)
    if error:
        view["error"] = error
    if klass:
        view["failure_class"] = klass[:40]
    if nxt is not None:
        view["next_retry_at"] = nxt
    if isinstance(failed.get("count"), int) and not isinstance(failed.get("count"), bool):
        view["attempts"] = failed["count"]
    if isinstance(failed.get("first_failure_at"), (int, float)) and not isinstance(failed.get("first_failure_at"), bool):
        view["first_failure_at"] = failed["first_failure_at"]
    if phase:
        view["phase"] = phase[:40]
    if retryable:
        view["recovery"] = "automatic"
        view["note"] = "scheduled checks retry this temporary failure"
        operation = "status"
    elif certificate:
        view["recovery"] = "action_required"
        view["note"] = "certificate trust needs repair; scheduled checks retry after backoff"
        operation = "status"
    elif actionable:
        view["recovery"] = "action_required"
        view["note"] = "credentials or host permission need attention"
        operation = "status"
    elif quarantined or unknown or phase == "package-refresh":
        view["recovery"] = "manual"
        view["note"] = "manual recovery is required for this revision"
        operation = "recover"
    else:
        operation = "status"
    command = _launcher_command(adapter, operation, current)
    if command:
        view["command"] = command
    return view


def _knowledge_status_payload(session=None):
    if not _configured():
        payload = three_choices()
        if payload["first_use"] in {"read-only", "later", "contribute"}:
            payload["repeat"] = True
            payload["choices"] = []
        return payload
    import consent as consent_mod
    from sharing import public_status

    try:
        sharing_view, saved = public_status(), consent_mod.load()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(
            configured=None,
            sharing=dict(enabled=None, status="unavailable"),
            error=dict(stage="local_settings", type=type(exc).__name__),
            config=str(config_path()),
            diagnostics=_diagnostics(session),
            hint="Inspect the existing adapter and community configuration. Native tools and independent SSH remain available; no setup or retry was started.",
        )
    choice = saved["choice"] if saved["state"] == "ok" else None
    payload = dict(configured=True, sharing=sharing_view, first_use=choice,
                   consent_state=saved["state"])
    diagnostics = _diagnostics(session)
    payload["diagnostics"] = diagnostics
    if session:
        admission = diagnostics.get("admission") or {}
        state = admission.get("status", "unavailable")
        payload["this_session"] = dict(
            status=state,
            bound=state == "active",
            enabled=state == "active" and admission.get("enabled") is True,
            failures=admission.get("failures"),
            project_root=admission.get("project_root"),
        )
    if choice:
        # The one-time setup is done; it is never presented again.
        payload["repeat"] = True
        payload["choices"] = []
    elif saved["state"] in {"corrupt", "unreadable"}:
        # Damaged saved state is a fault, never a fresh install: read-only
        # help keeps working, writes stop, no re-onboarding. Nothing is
        # rewritten automatically — repair is the user's explicit action.
        payload["repeat"] = True
        payload["choices"] = []
        payload["consent_error"] = dict(state=saved["state"], error=saved["error"])
        payload["hint"] = (
            f"The saved setup state is damaged ({saved['path']}). This is NOT "
            "a fresh install: read-only knowledge keeps working and nothing "
            "is collected. Nothing was changed automatically: move that file "
            "aside (keep it as evidence) or delete it, then make your choice "
            "again via /mindie-agent."
        )
    elif sharing_view.get("state") == "corrupt" or sharing_view.get("error"):
        # A damaged settings file is a fault, never a fresh install.
        payload["repeat"] = True
        payload["choices"] = []
        payload["hint"] = (
            "The saved community settings are damaged. Read-only knowledge "
            "keeps working and nothing is collected; repair the file or "
            "change settings explicitly via /mindie-agent."
        )
    elif saved["state"] == "missing" and consent_mod.marker_exists():
        # A legacy marker (any state) proves a prior setup: status, never a
        # fresh onboarding — a damaged marker must not re-ask the choice.
        payload["repeat"] = True
        payload["choices"] = []
    elif not payload["sharing"].get("enabled"):
        # Genuinely unchosen: cold install or installer default-off. The
        # one-time setup is presented exactly until a choice is recorded.
        extra = three_choices()
        payload["choices"] = extra["choices"]
        payload["note"] = extra["note"]
        payload["setup"] = extra["setup"]
        payload["hint"] = (
            "Sharing is off: no Stop capture or organizer. "
            "Knowledge retrieval works after invoking /mindie-agent once in this task."
        )
    update = _updater_view()
    if update:
        payload["update"] = update
    return payload


def status_payload(session=None):
    """Read-only. Reporting is independent of knowledge setup and binding."""
    import consent as consent_mod
    import diagnostic_support

    payload = dict(_knowledge_status_payload(session))
    payload["reporting"] = diagnostic_support.reporting_status()
    saved = consent_mod.load()
    # Reporting is offered once, inside the first setup, never repeatedly.
    if (
        payload["reporting"].get("status") == "not_configured"
        and saved["state"] == "missing"
        and not consent_mod.install_traces()
    ):
        payload["reporting_choice"] = diagnostic_support.reporting_hint()
    return payload


def _project_root(session, cwd):
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return str(Path(cwd).resolve())
    try:
        return str(Path(session_cwd(session)).resolve())
    except ValueError:
        raise ValueError("native session cwd is required to activate; no caller task id is accepted")


def _adopt_install_state():
    """Explicit install/upgrade/entry adoption boundary: converge community
    settings onto the profile-shared authority, then import a legacy consent
    choice exactly once (evidence is read from the converged authority).
    Reads elsewhere never perform these writes. Only meaningful results are
    reported; conflicts stay diagnosable and unresolved."""
    import consent as consent_mod

    report = {}
    try:
        community = consent_mod.adopt_community_settings()
        if community.get("action") not in {"already"} or community.get("conflict"):
            report["community"] = community
    except Exception as exc:
        report["community"] = dict(action="error", error=type(exc).__name__)
    try:
        choice = consent_mod.adopt_legacy_choice()
        if choice.get("action") not in {"already", "unchosen"}:
            report["consent"] = choice
    except Exception as exc:
        report["consent"] = dict(action="error", error=type(exc).__name__)
    return report or None


def _parse_native_setup(arguments):
    """Native entry arguments: empty, ``read-only``/``later``, or
    ``contribute <owner/repo> <account>``. Values come from the native
    commandArgs/skillArgs — never from model arguments."""
    text = (arguments or "").strip()
    if not text:
        return None
    tokens = text.split()
    if tokens[0] in {"read-only", "later"}:
        return dict(choice=tokens[0])
    if tokens[0] == "contribute":
        if len(tokens) != 3:
            raise ValueError(
                "contribute uses native arguments: /mindie-agent contribute owner/repo account"
            )
        return dict(choice="contribute", repository=tokens[1], account=tokens[2])
    return None


def _enable_contribution(session, cwd, *, repository, account, branch="main", fork=None):
    """The explicit public opt-in: enable sharing for the current project.
    The project root is the native session cwd (host-derived — never a
    command or model argument). An existing scope for the SAME destination
    is preserved and the current project is added — never silently widened
    to another repository."""
    import sharing as sharing_mod
    from mindie_knowledge.community.common import check_account
    from mindie_knowledge.loop.settings import check_branch, check_repository

    repository = check_repository(repository)
    branch = check_branch(branch)
    try:
        account = check_account(account)
    except Exception as exc:
        raise ValueError(str(exc)[:200]) from None
    root = _project_root(session, cwd)
    roots = [root]
    try:
        existing = sharing_mod.load()
        if existing.enabled and existing.repository == repository:
            for item in existing.project_roots:
                item = str(item)
                if item not in roots:
                    roots.append(item)
    except Exception:
        pass
    return sharing_mod.write_enabled(
        repository=repository, project_roots=roots, branch=branch,
        visibility="public", account=account, fork=fork)


def _apply_choice(session, cwd, choice, *, repository=None, account=None,
                  branch="main", fork=None):
    """Record a verified choice and keep the sharing settings consistent
    with it: contribution enables capture for the current project; any
    other choice leaves sharing off, so a revoked contribution stops for
    real. A damaged consent document surfaces as an honest fault — nothing
    is rewritten automatically. Returns the sharing view (or None)."""
    import consent as consent_mod

    if choice == "contribute" and not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    consent_mod.record_choice(choice)
    view = None
    if _configured():
        import sharing as sharing_mod

        if choice == "contribute":
            view = _enable_contribution(
                session, cwd, repository=repository, account=account,
                branch=branch, fork=fork,
            )
        else:
            view = sharing_mod.write_disabled()
    return view


def _apply_reporting(value):
    """Persist the reporting choice and keep the real reporter consistent:
    enable/disable reconfigure the actual service; ``later`` turns an
    enabled reporter off and only then persists the preference. Enabling is
    refused when the consent document is damaged (the preference could not
    be persisted); turning off is always allowed, with the persistence gap
    surfaced instead of silently rewritten."""
    import consent as consent_mod
    import diagnostic_support

    if value not in consent_mod.REPORTING:
        raise ValueError("reporting must be enabled, disabled or later")
    damaged = consent_mod.load()["state"] in {"corrupt", "unreadable"}
    if damaged and value == "enabled":
        raise ValueError(
            "the saved setup state is damaged; reporting was not enabled. "
            "Move the damaged consent file aside and choose again."
        )
    if not _configured():
        if value == "later":
            if not damaged:
                consent_mod.record_reporting("later")
            return dict(reporting="later" if not damaged else "not-recorded")
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    python = load_adapter_config()["python"]
    # "later" means not-on: an enabled reporter is really turned off.
    expected = value == "enabled"
    result = diagnostic_support.configure_reporting(expected, python)
    if result.get("enabled") is not expected:
        return result  # unconfirmed service state: nothing is recorded
    if damaged:
        return dict(
            result,
            preference="not-recorded: the saved setup state is damaged; "
            "the reporter is off, the preference was not persisted",
        )
    consent_mod.record_reporting(value)
    return result


def _configured_init_activation(session, cwd, activation_id):
    """Entry binding for a configured task: automatic, idempotent, and never
    a consent prompt. The persistent install-level choice is untouched."""
    from admission import activate, gate
    from knowledge_service import ensure_service

    root = _project_root(session, cwd)
    lease = activate(session, project_root=root, root_session=session)
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
    payload["binding"] = dict(
        session=lease["session"],
        enabled=bool(lease.get("enabled")),
        project_root=lease["project_root"],
        service=service,
    )
    return payload


def _apply_entry_choice(payload, session, cwd, native):
    view = _apply_choice(
        session, cwd, native["choice"],
        repository=native.get("repository"), account=native.get("account"),
    )
    payload["first_use"] = native["choice"]
    payload["choices"] = []
    payload["repeat"] = True
    if view is not None:
        payload["sharing"] = view
    return payload


def op_init(session, cwd):
    found = require_current_entry(session, "init")
    native = _parse_native_setup(found.get("arguments"))
    if not _configured():
        if not consume_activation_id(found["activation_id"]):
            payload = status_payload(session)
            payload["already"] = True
            return payload
        if native is None:
            return status_payload(session)
        if native["choice"] == "contribute":
            raise ValueError("MindIE is not configured; run scripts/setup.py first")
        payload = status_payload(session)
        return _apply_entry_choice(payload, session, cwd, native)
    migration = _adopt_install_state()
    payload = _configured_init_activation(session, cwd, found["activation_id"])
    if migration:
        payload["migration"] = migration
    if native is not None:
        payload = _apply_entry_choice(payload, session, cwd, native)
    return payload


def op_choose(session, choice, cwd=None, *, repository=None, account=None,
              reporting=None):
    """Store a setup choice from the unified-entry conversation.

    An ordinary user reply (kind=user) may make the FIRST choice; changing
    an existing saved choice requires the verified entry itself (the user
    explicitly changing settings). A conversational contribution
    destination must appear in the user's own current reply; via the entry
    it comes from the native arguments only. A damaged consent document is
    an honest fault — nothing is rewritten automatically.
    """
    import consent as consent_mod

    migration = _adopt_install_state() if _configured() else None
    openings, origin, user_text = current_entry_scan(session)
    found = None
    if origin.get("kind") == "user":
        saved = consent_mod.load()
        if saved["state"] == "ok" and saved["choice"] is not None:
            raise ValueError(
                "a choice is already saved; change settings explicitly via "
                "the entry, e.g. /mindie-agent read-only"
            )
    else:
        user_text = None
        found = _entry_from_openings(openings, "init")
    if choice == "contribute":
        if user_text is not None:
            if not repository or not account:
                raise ValueError(
                    "contribute requires the repository and account the user stated"
                )
            if repository not in user_text or account not in user_text:
                raise ValueError(
                    "the public contribution destination must appear in the "
                    "user's current reply"
                )
        else:
            native = _parse_native_setup(found.get("arguments"))
            if native is None or native.get("choice") != "contribute":
                raise ValueError(
                    "contribution via the entry uses native arguments: "
                    "/mindie-agent contribute owner/repo account"
                )
            repository, account = native["repository"], native["account"]
    view = None
    if choice is not None:
        view = _apply_choice(
            session, cwd, choice, repository=repository, account=account
        )
    reporting_view = _apply_reporting(reporting) if reporting is not None else None
    payload = status_payload(session)
    if choice is not None:
        payload["first_use"] = choice
    payload["choices"] = []
    payload["repeat"] = True
    if view is not None:
        payload["sharing"] = view
    if reporting_view is not None:
        payload["reporting_result"] = reporting_view
    if migration:
        payload["migration"] = migration
    return payload


def op_status(session):
    # A bound task that explicitly enabled MindIE can diagnose a later failure
    # without another user slash command. This read does not consume an
    # attempt or grant/rebind anything.
    admitted = False
    if _configured():
        from admission import gate

        try:
            admitted = gate().inspect(session).get("status") == "active"
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


def op_sharing_enable(session, _model_arguments="", cwd=None):
    found, first = consume_slash(session, "sharing-enable")
    if not first:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    if not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    _adopt_install_state()
    parsed = _parse_sharing(found.get("arguments") or "")
    root = _project_root(session, cwd)
    for item in parsed["project_roots"]:
        if item != root:
            raise ValueError(
                "the contribution scope is always the current project "
                "(native session cwd); --project-root is not accepted"
            )
    return _apply_choice(
        session, cwd, "contribute",
        repository=parsed["repository"], account=parsed["account"],
        branch=parsed["branch"], fork=parsed["fork"],
    )


def op_sharing_disable(session):
    consume_slash(session, "sharing-disable")
    if not _configured():
        return dict(configured=False, enabled=False)
    import sharing as sharing_mod

    _adopt_install_state()
    result = sharing_mod.write_disabled()
    import consent as consent_mod

    consent_mod.record_choice("disabled")
    return result


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
    return _apply_reporting("enabled" if command == "reporting-enable" else "disabled")


def dispatch(session, cwd, op, arguments="", choice=None, *, repository=None,
             account=None, reporting=None):
    if op == "init":
        return op_init(session, cwd)
    if op == "choose":
        if choice is None and reporting is None:
            raise ValueError("choose requires a choice or a reporting value")
        if choice is not None and choice not in {"contribute", "read-only", "later", "disabled"}:
            raise ValueError(
                "choose requires choice=contribute, read-only, later or disabled"
            )
        return op_choose(
            session, choice, cwd, repository=repository, account=account,
            reporting=reporting,
        )
    if op == "status":
        return op_status(session)
    if op == "deactivate":
        return op_deactivate(session)
    if op == "sharing-enable":
        return op_sharing_enable(session, arguments, cwd)
    if op == "sharing-disable":
        return op_sharing_disable(session)
    if op == "sharing-status":
        return op_sharing_status(session)
    if op == "recover":
        return op_recover(session, arguments)
    if op in {"reporting-status", "reporting-enable", "reporting-disable"}:
        return op_reporting(session, op)
    raise ValueError("unknown MindIE entry operation")
