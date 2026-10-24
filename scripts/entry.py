"""Slash-command and unified-entry operations for `mindie_entry` MCP tool.

[Role]
Implements `dispatch` (`init`, `choose`, `status`, `deactivate`,
`sharing-enable`, `sharing-disable`, `sharing-status`, `recover`,
`reporting-status`) after `mcp_server`
claims the native `PreToolUse` nonce.

[Invariants]
- Unconfigured `init`/`status`/`choose` execute with stdlib only (no runtime
  knowledge imports or background service starts).
- Public contribution destination (`owner/repo` and `account`) must originate
  from the user's current turn reply or native slash arguments, never model-only
  tool arguments.
- Explicit `init` domain activation uses the native verified session/cwd and
  the target config's own consent/scope/admission. It never writes or rotates
  the default-domain lease token.
- `status_payload` is strictly read-only and never mutates consent or community
  files; install-state convergence runs only on explicit entry/sharing mutations.

[Timeouts & Locks]
- `op_recover` runs each `mindie_knowledge.loop.cli` recovery subcommand with a
  `20s` timeout via `bounded.run`.
- Registered-domain deactivation delegates one shared total `6s` revocation
  budget to the core helper; the outer explicit-entry child budget covers the
  helper plus cleanup.

[Side-Effect Policy]
- Mutates task admission leases, install-level consent/community settings, and
  starts the background knowledge service when `allows_capture()` is true.
  Explicit target-domain activation writes only that domain's admission lease;
  native deactivation revokes the trusted session across default and registered
  domain admissions without consulting capture consent.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from entry_state import (
    _number,
    _read_regular_json,
    configuration_required,
    consume_activation_id,
)
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
    except TimeoutError:
        raise
    except Exception:
        token = None
    if token is None:
        return found, consume_activation_id(found["activation_id"])
    return found, gate().claim(session, "plugin_command", found["activation_id"], token=token) is True


def _diagnostics(session):
    try:
        from mindie_knowledge.loop.diagnostics import snapshot

        return snapshot(str(engine_config_path()), session=session)
    except TimeoutError:
        raise
    except Exception as exc:
        return dict(
            status="unavailable",
            error=dict(stage="runtime_diagnostics", type=type(exc).__name__),
            hints=[
                "Inspect the selected MindIE runtime. Native tools and independent SSH remain available; no recovery or retry was started."
            ],
        )


def _bounded_text(value, limit):
    return value[:limit] if isinstance(value, str) and value else None


def _launcher_command(adapter, operation, current):
    try:
        from genstate import launch_dir

        if not isinstance(current, dict):
            return None
        sha = current.get("sha") if isinstance(current.get("sha"), str) and current.get("sha") else "bootstrap"
        python = current.get("python")
        if not isinstance(python, str) or not python:
            return None
        launcher = launch_dir(adapter) / sha / "mindie_launch.py"
        if not launcher.is_file():
            return None
        config_value = adapter.get("base_config") if isinstance(adapter.get("base_config"), str) else str(config_path())
        config_file = str(Path(config_value).expanduser().resolve())
        return shlex.join([python, str(launcher.resolve()), "--config", config_file, "updater", operation])
    except TimeoutError:
        raise
    except Exception:
        return None


def _updater_view():
    try:
        from genstate import current_path, failed_path, status_path

        adapter = load_adapter_config()
        if not isinstance(adapter, dict):
            return {"status": "unavailable"}
        failed_state, failed = _read_regular_json(failed_path(adapter))
        status_state, status = _read_regular_json(status_path(adapter))
        current_state, current = _read_regular_json(current_path(adapter))
    except TimeoutError:
        raise
    except Exception:
        return {"status": "unavailable"}
    if "unavailable" in {failed_state, status_state, current_state}:
        return {"status": "unavailable"}
    failed = failed if isinstance(failed, dict) else {}
    status = status if isinstance(status, dict) else {}
    current = current if isinstance(current, dict) else {}
    klass = failed.get("failure_class") if isinstance(failed.get("failure_class"), str) else None
    if klass is None and isinstance(status.get("resolve_failure_class"), str):
        klass = status.get("resolve_failure_class")
    phase = failed.get("phase") if isinstance(failed.get("phase"), str) else None
    certificate = klass == "certificate"
    actionable = klass in {"authentication", "permission", "hook_trust", "certificate"}
    nxt = _number(failed.get("next_retry_at"))
    if nxt is None:
        nxt = _number(status.get("next_retry_at"))
    active = bool(
        failed.get("error") or klass or phase or nxt is not None
        or status.get("error") or status.get("resolve_wait")
        or status.get("result") in {"failed", "check-failed", "action-required", "retry-waiting"}
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
    if error := _bounded_text(failed.get("error"), 200) or _bounded_text(status.get("error"), 200):
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
    if certificate:
        view["recovery"] = "action_required"
        view["note"] = "certificate trust needs repair; scheduled checks retry after backoff"
    elif actionable:
        view["recovery"] = "action_required"
        view["note"] = "credentials or host permission need attention"
    else:
        view["recovery"] = "automatic"
        view["note"] = "scheduled checks retry this failure after backoff"
    if command := _launcher_command(adapter, "status", current):
        view["command"] = command
    return view


def _knowledge_status_payload(session=None):
    if not _configured():
        payload = configuration_required()
        if payload["first_use"] in {"read-only", "later", "contribute"}:
            payload["repeat"] = True
            payload["choices"] = []
        return payload
    import consent as consent_mod
    from sharing import public_status

    try:
        sharing_view, saved = public_status(), consent_mod.load()
    except TimeoutError:
        raise
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
    payload = dict(configured=True, sharing=sharing_view, first_use=choice, consent_state=saved["state"])
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
    payload["choices"] = []
    payload["repeat"] = bool(
        choice or saved["state"] in {"corrupt", "unreadable"}
        or sharing_view.get("state") in {"corrupt", "unreadable"}
        or consent_mod.marker_exists()
    )
    if saved["state"] in {"corrupt", "unreadable"}:
        payload["experience"] = "unavailable"
        payload["consent_error"] = dict(state=saved["state"], error=saved["error"])
        payload["hint"] = "Saved setup state is damaged; experience capture is unavailable. Preserve the file for diagnosis."
    elif sharing_view.get("state") in {"corrupt", "unreadable"}:
        payload["experience"] = "unavailable"
        payload["hint"] = "Community configuration is damaged; experience capture is unavailable."
    elif choice in {"read-only", "later", "disabled"}:
        payload["experience"] = "disabled"
        payload["hint"] = "Experience capture is explicitly disabled; the saved setting is preserved."
    elif not sharing_view.get("enabled") or not choice:
        payload["experience"] = "needs-configuration"
        extra = configuration_required()
        payload.update(required=extra["required"], note=extra["note"])
        payload["hint"] = "Configure the missing destination and scope through the native mindie-agent entry."
    else:
        from sharing import load

        settings = load()
        task = payload.get("this_session") or {}
        if not settings.allows_capture():
            payload["experience"] = "unavailable"
        elif not task.get("bound"):
            payload["experience"] = "task-unbound"
        elif not settings.in_scope(task.get("project_root")):
            payload["experience"] = "out-of-scope"
        else:
            payload["experience"] = "configured"
        payload["hint"] = "Configuration and task binding are prerequisites; inspect captures and contributions for actual processing receipts."
    if update := _updater_view():
        payload["update"] = update
    return payload


def status_payload(session=None):
    import diagnostic_support

    payload = dict(_knowledge_status_payload(session))
    payload["reporting"] = diagnostic_support.reporting_status()
    return payload


def status_facts(session=None):
    """Deterministic control-plane facts, without knowledge setup guidance."""
    payload = dict(configured=_configured(), knowledge_enabled=False)
    if payload["configured"] and session:
        from admission import gate
        lease = gate().knowledge_lease(session)
        payload["knowledge_enabled"] = lease is not None
        payload["this_session"] = dict(bound=lease is not None, enabled=lease is not None,
                                       status="active" if lease else "inactive",
                                       project_root=lease["project_root"] if lease else None)
    if payload["configured"]:
        diagnostics = _diagnostics(session)
        payload["diagnostics"] = {key: value for key, value in diagnostics.items() if key != "hints"}
        if session:
            payload["this_session"]["failures"] = (diagnostics.get("admission") or {}).get("failures")
    import diagnostic_support
    payload["reporting"] = diagnostic_support.reporting_status()
    return payload


def _project_root(session, cwd):
    if isinstance(cwd, str) and os.path.isabs(cwd):
        return str(Path(cwd).resolve())
    try:
        return str(Path(session_cwd(session)).resolve())
    except ValueError:
        raise ValueError("native session cwd is required to activate; no caller task id is accepted")


def _adopt_install_state():
    import consent as consent_mod

    report = {}
    try:
        community = consent_mod.adopt_community_settings()
        if community.get("action") not in {"already"} or community.get("conflict"):
            report["community"] = community
    except TimeoutError:
        raise
    except Exception as exc:
        report["community"] = dict(action="error", error=type(exc).__name__)
    try:
        stranded = consent_mod.adopt_stranded_consent()
        if stranded.get("action") not in {"already"} or stranded.get("notes"):
            report["consent"] = stranded
    except TimeoutError:
        raise
    except Exception as exc:
        report["consent"] = dict(action="error", error=type(exc).__name__)
    return report or None


def _parse_native_setup(arguments):
    text = (arguments or "").strip()
    if not text:
        return None
    tokens = text.split()
    if tokens[0] in {"read-only", "later"}:
        raise ValueError("read-only/later product modes were removed; configure the destination and scope, or use disabled")
    if tokens[0] == "disabled":
        return dict(choice=tokens[0])
    if tokens[0] == "contribute":
        if len(tokens) != 3:
            raise ValueError("contribute uses native arguments: /mindie-agent contribute owner/repo account")
        return dict(choice="contribute", repository=tokens[1], account=tokens[2])
    return None


def _enable_contribution(session, cwd, *, repository, account, branch="main", fork=None):
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
    return sharing_mod.write_enabled(
        repository=repository, project_roots=[root], branch=branch,
        visibility="public", account=account, fork=fork, extend_scope=True,
    )


def _apply_choice(session, cwd, choice, *, repository=None, account=None, branch="main", fork=None):
    import consent as consent_mod

    if choice not in {"contribute", "disabled"}:
        raise ValueError("configure contribution or explicitly disable it; read-only/later are legacy data only")
    if choice == "contribute" and not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    if consent_mod.load()["state"] == "unreadable":
        raise ValueError("saved setup state is unreadable; configuration was not changed")
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
    consent_mod.record_choice(choice)
    import diagnostic_support

    reporting = diagnostic_support.activate_reporting()
    view = dict(view or {}, reporting=reporting)
    if choice == "contribute" and view is not None:
        from admission import gate
        from knowledge_service import ensure_service

        if gate().inspect(session).get("status") == "active":
            try:
                ensure_service(engine_config_path())
                view["service"] = "started"
            except TimeoutError:
                raise
            except Exception as exc:
                view["service"] = f"not-started:{type(exc).__name__}"
    return view


def _configured_init_activation(session, cwd, activation_id, *, activate_reporter=True):
    from admission import activate, gate
    from knowledge_service import ensure_service

    root = _project_root(session, cwd)
    lease = activate(session, project_root=root, root_session=session)
    if gate().claim(session, "plugin_command", activation_id, token=lease["token"]) is not True:
        payload = status_payload(session)
        payload["already"] = True
        return payload
    try:
        from sharing import load

        if load().allows_capture():
            ensure_service(engine_config_path())
            service = "started"
        else:
            service = "not-requested:configuration-incomplete-or-disabled"
    except TimeoutError:
        raise
    except Exception as exc:
        service = f"not-started:{type(exc).__name__}"
    import diagnostic_support

    payload = status_payload(session)
    if activate_reporter:
        payload["reporting_activation"] = diagnostic_support.activate_reporting()
    payload["binding"] = dict(
        session=lease["session"],
        enabled=bool(lease.get("enabled")),
        project_root=lease["project_root"],
        service=service,
    )
    if str(service).startswith("not-started:"):
        payload["status"] = "degraded"
        payload["experience"] = "unavailable"
        payload["warning"] = f"knowledge capture service could not be started ({service})"
    return payload


def _configured_domain_activation(session, cwd, domain, found):
    """Bind one explicitly selected target domain without touching the default."""
    if not _configured():
        raise ValueError("MindIE is not configured; run scripts/setup.py first")
    if _parse_native_setup(found.get("arguments")) is not None:
        raise ValueError("domain activation cannot be combined with setup arguments")
    root = _project_root(session, cwd)
    try:
        from mindie_knowledge.loop.domain_routes import activate_selected_domain
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        raise ValueError(
            "knowledge domain activation is unavailable in the installed runtime"
        ) from exc
    from mindie_knowledge.loop.activation import Admission
    from paths import admission_path, domain_configs, engine_config_path, load_engine_config_at

    result = activate_selected_domain(
        str(engine_config_path()),
        domain=domain,
        domain_configs=domain_configs(),
        session=session,
        project_root=root,
        root_session=session,
    )
    lease = result["lease"]
    target_engine = load_engine_config_at(result["engine_config"])
    first = Admission(admission_path(target_engine)).claim(
        session, "plugin_command", found["activation_id"], token=lease["token"],
    )
    payload = status_payload(session)
    payload["binding"] = dict(
        domain=result["domain"],
        session=lease.get("session"),
        enabled=bool(lease.get("enabled")),
        project_root=lease.get("project_root"),
        generation=result.get("generation"),
        service="not-requested:target-service-starts-on-use",
    )
    if first is not True:
        payload["already"] = True
    return payload


def _apply_entry_choice(payload, session, cwd, native):
    view = _apply_choice(
        session, cwd, native["choice"],
        repository=native.get("repository"), account=native.get("account"),
    )
    payload.update(status_payload(session))
    payload["first_use"] = native["choice"]
    payload["choices"] = []
    payload["repeat"] = True
    if view is not None:
        payload["sharing"] = view
        if str(view.get("service", "")).startswith("not-started:"):
            payload["status"] = "degraded"
            payload["experience"] = "unavailable"
            payload["warning"] = f"knowledge capture service could not be started ({view.get('service')})"
    return payload


def op_init(session, cwd, domain=None):
    found = require_current_entry(session, "init")
    if domain is not None:
        return _configured_domain_activation(session, cwd, domain, found)
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
        return _apply_entry_choice(status_payload(session), session, cwd, native)
    migration = _adopt_install_state()
    payload = _configured_init_activation(
        session, cwd, found["activation_id"], activate_reporter=native is None,
    )
    if migration:
        payload["migration"] = migration
    if native is not None:
        payload = _apply_entry_choice(payload, session, cwd, native)
    return payload


def op_choose(session, choice, cwd=None, *, repository=None, account=None):
    import consent as consent_mod

    migration = _adopt_install_state() if _configured() else None
    openings, origin, user_text = current_entry_scan(session)
    found = None
    if origin.get("kind") == "user":
        saved = consent_mod.load()
        if saved["state"] == "ok" and saved["choice"] is not None:
            raise ValueError(
                "a choice is already saved; change settings explicitly via "
                "the entry, e.g. /mindie-agent contribute owner/repo account"
            )
    else:
        user_text = None
        found = _entry_from_openings(openings, "init")
    if choice == "contribute":
        if user_text is not None:
            if not repository or not account:
                raise ValueError("contribute requires the repository and account the user stated")
            if repository not in user_text or account not in user_text:
                raise ValueError("the public contribution destination must appear in the user's current reply")
        else:
            native = _parse_native_setup(found.get("arguments"))
            if native is None or native.get("choice") != "contribute":
                raise ValueError(
                    "contribution via the entry uses native arguments: "
                    "/mindie-agent contribute owner/repo account"
                )
            repository, account = native["repository"], native["account"]
    view = _apply_choice(session, cwd, choice, repository=repository, account=account) if choice is not None else None
    payload = status_payload(session)
    if choice is not None:
        payload["first_use"] = choice
    payload["choices"] = []
    payload["repeat"] = True
    if view is not None:
        payload["sharing"] = view
        if str(view.get("service", "")).startswith("not-started:"):
            payload["status"] = "degraded"
            payload["experience"] = "unavailable"
            payload["warning"] = f"knowledge capture service could not be started ({view.get('service')})"
    if migration:
        payload["migration"] = migration
    return payload


def op_status(session):
    return status_facts(session)


def op_deactivate(session):
    consume_slash(session, "deactivate")
    if not _configured():
        return dict(session=session, deactivated=False, configured=False)
    from admission import deactivate
    from paths import domain_configs, engine_config_path

    mappings = domain_configs()
    if not mappings:
        return dict(session=session, deactivated=bool(deactivate(session)))
    try:
        from mindie_knowledge.loop.domain_routes import deactivate_registered_domains
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        raise ValueError(
            "knowledge domain deactivation is unavailable in the installed runtime"
        ) from exc
    result = deactivate_registered_domains(
        str(engine_config_path()),
        domain_configs=mappings,
        session=session,
        timeout=6.0,
    )
    domains = [
        {key: value for key, value in item.items() if key != "engine_config"}
        for item in result.get("domains", [])
        if isinstance(item, dict)
    ]
    payload = dict(
        session=session,
        deactivated=result.get("status") == "inactive" and bool(result.get("changed")),
        changed=bool(result.get("changed")),
        status=result.get("status"),
        domains=domains,
    )
    if result.get("status") != "inactive":
        payload["partial"] = True
        payload["warning"] = (
            "one or more registered domains could not be revoked; inspect domains"
        )
    return payload


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
    _adopt_install_state()
    return _apply_choice(session, None, "disabled")


def op_sharing_status(session):
    consume_slash(session, "sharing-status")
    if not _configured():
        return dict(configured=False, enabled=False)
    from sharing import public_status

    return dict(
        **public_status(), diagnostics=_diagnostics(session),
        hint="The worker handles eligible transient recovery automatically. Diagnostics identify faults requiring action; batch commands are optional operator troubleshooting.",
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
    for operation in ("contribution-inspect", "contribution-reconcile", "contribution-compact"):
        try:
            output = run([python, "-m", "mindie_knowledge.loop.cli", operation, "--config", engine, *extra], "", timeout=20)
            result[operation] = json.loads(output)
            result["actions"].append(operation)
        except TimeoutError:
            raise
        except Exception as exc:
            receipt = None
            if getattr(exc, "returncode", None) == 2:
                try:
                    receipt = json.loads(getattr(exc, "mindie_output", "") or "")
                except ValueError:
                    receipt = None
            if isinstance(receipt, dict):
                result[operation] = receipt
                result[operation + "_exit"] = 2
                result["actions"].append(operation)
            else:
                result[operation + "_error"] = str(exc)[:200]
    return result


def op_reporting(session):
    consume_slash(session, "reporting-status")
    import diagnostic_support

    return diagnostic_support.reporting_status()


def dispatch(session, cwd, op, arguments="", choice=None, *, repository=None, account=None, domain=None):
    if op != "init" and domain is not None:
        raise ValueError("domain is accepted only with op=init")
    if op == "init":
        return op_init(session, cwd, domain=domain)
    if op == "choose":
        if choice is None:
            raise ValueError("choose requires the contribution choice")
        if choice is not None and choice not in {"contribute", "read-only", "later", "disabled"}:
            raise ValueError("choose requires choice=contribute, read-only, later or disabled")
        return op_choose(session, choice, cwd, repository=repository, account=account)
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
    if op == "reporting-status":
        return op_reporting(session)
    raise ValueError("unknown MindIE entry operation")
