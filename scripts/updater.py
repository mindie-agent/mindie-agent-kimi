#!/usr/bin/env python3
"""MindIE Kimi automatic updater and feed-sync runner.

[Role]
Executes a single bounded update check (`check`), status inspection (`status`),
explicit recovery (`recover`), or scheduler registration (`install-schedule` /
`uninstall-schedule`). Selects a compatible formal Release, stages its exact generation
with its own pinned venv and versioned launcher, switches `current.json` under
the exclusive `operation.lock` after `stop_if_idle` and native plugin install
readback, and maintains diagnostic retention.

[Invariants]
- Live entrypoints are never mutated during staging; each generation gets a
  versioned launcher under `update/launch/<sha>/mindie_launch.py`.
- Switch requires an idle knowledge service (`stop_if_idle`), verified native
  plugin readback, and atomic `current.json` replacement; failures roll back both
  `current.json` and the native plugin package under the same exclusive lock.
- Diagnostic retention runs inside its reserved tail budget even when candidate
  preparation fails. Release checks never authorize knowledge feed activity.

[Timeouts & Locks]
- `CHECK_BUDGET = 240.0s`, `ROLLBACK_BUDGET = 45.0s`, `FEED_BUDGET = 60.0s`,
  `HANDOFF_BUDGET = 8.0s` (`RESERVE = 113.0s`), `INTERVAL_SECONDS = 300`.
- Serialized by non-blocking `check.lock`; generation switch acquires exclusive
  `operation.lock` (default `30.0s` wait).

[Side-Effect Policy]
- Stages generations under `update/generations/<sha>`, updates `current.json`,
  `status.json`, `failed.json`, and `receipts/<sha>.json`, installs native Kimi
  plugin packages, and manages launchd / Windows Task Scheduler entries.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import diagnostic_support

try:
    from bounded import CommandTimedOut
    from bounded import run as bounded_run
    from genstate import (
        LockTimeout,
        OperationLock,
        atomic_write,
        check_lock,
        current_path,
        failed_path,
        generations_dir,
        launch_dir,
        read_current,
        read_json,
        read_status,
        receipts_dir,
        update_dir,
        write_current,
        write_status,
    )
    from paths import (
        config_path,
        load_adapter_config,
        load_engine_config,
    )
except ImportError as exc:
    diagnostic_support.failure("updater", "bootstrap_import", "missing_committed_file", exception=exc)
    raise SystemExit("committed updater dependency is unavailable") from None

DEFAULT_REMOTE = "https://github.com/mindie-agent/mindie-agent-kimi.git"
INTERVAL_SECONDS = 300
CHECK_BUDGET = 240.0
ROLLBACK_BUDGET = 45.0
FEED_BUDGET = 60.0
HANDOFF_BUDGET = 8.0
RESERVE = ROLLBACK_BUDGET + FEED_BUDGET + HANDOFF_BUDGET
SHA = r"[0-9a-f]{40}"
COMPLETE = ".mindie-generation-complete"
LAUNCHER = "mindie_launch.py"
MIN_PYTHON = (3, 12) if os.name == "nt" else (3, 11)


class CheckFailed(RuntimeError):
    """A failed check: suppress this exact revision until recover."""


class Deferred(RuntimeError):
    """Not applied now; next normal scheduled check may retry."""


def _remaining(deadline: float, reserve: float = 0.0) -> float:
    left = deadline - time.monotonic() - reserve
    if left < 5:
        raise CheckFailed("whole-check deadline exhausted")
    return left


def _op_timeout(deadline: float, reserve: float, cap: float) -> float:
    left = deadline - reserve - time.monotonic()
    if left <= 0:
        raise CheckFailed("no time left in this operation's reserved window")
    return min(cap, left)


def _git_bin():
    if explicit := os.environ.get("MINDIE_KIMI_GIT_BIN"):
        return explicit
    try:
        stamped = load_adapter_config().get("git_bin")
        if isinstance(stamped, str) and stamped:
            return stamped
    except TimeoutError:
        raise
    except (OSError, ValueError, FileNotFoundError):
        pass
    try:
        from paths import resolve_git_bin

        resolved = resolve_git_bin()
        if isinstance(resolved, str) and resolved:
            return resolved
    except TimeoutError:
        raise
    except (OSError, ValueError):
        pass
    return "git"


def _git(args, *, timeout, deadline, cwd=None, transport=False):
    return bounded_run(
        [_git_bin(), *args], "",
        timeout=min(timeout, _remaining(deadline, RESERVE)),
        cwd=cwd, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), transport=transport,
    )


def _installed_version(current: dict) -> str:
    source = Path(current["generation"]) if current.get("generation") else HERE.parent
    return json.loads((source / "kimi.plugin.json").read_text(encoding="utf-8"))["version"]


def _release_catalog(adapter: dict, current: dict, deadline: float) -> dict:
    from paths import resolve_kimi_bin

    kimi = resolve_kimi_bin(adapter)
    if not (isinstance(kimi, str) and kimi):
        raise CheckFailed("adapter configuration lacks kimi_bin for release selection")
    # Bound all pagination, asset and source reads together, including cleanup.
    output = bounded_run(
        [sys.executable, str(HERE / "release_metadata.py"), "select",
         "--kimi-bin", kimi,
         "--installed-version", _installed_version(current)],
        "", timeout=min(60, _remaining(deadline, RESERVE + 2)), transport=True,
    )
    return json.loads(output)


def resolve_release(remote: str, adapter: dict, current: dict, deadline: float) -> dict:
    selected = _release_catalog(adapter, current, deadline)
    candidate = selected["candidate"]
    if candidate is not None:
        tag = "refs/tags/" + candidate["tag_name"]
        output = _git(["ls-remote", remote, tag, tag + "^{}"],
                      timeout=30, deadline=deadline, transport=True)
        refs = dict((ref, sha) for sha, ref in (line.split() for line in output.splitlines()))
        sha = refs.get(tag + "^{}", refs.get(tag))
        if sha != candidate["metadata"]["source"]["sha"]:
            raise CheckFailed("formal Release tag does not match its declared source SHA")
    return selected


import updater_check
import updater_schedule
import updater_staging
from updater_check import (
    _FEED_ERROR,
    _FEED_KNOWN,
    _FEED_OK,
    _FEED_PENDING,
    _check_once,
    _feed_error_summary,
    _feed_sync,
    _logging_maintenance,
    check,
    fold_feed_results,
)
from updater_schedule import (
    _launchd_state,
    _native_run,
    _register_windows_schedule,
    _schedule_failed,
    _schedule_wrapper,
    _task_state,
    _uninstall_launchd,
    _uninstall_task,
    _verify_scheduled_task,
    _wrapper_body,
    install_schedule,
    uninstall_schedule,
)
from updater_staging import (
    _profile_authority_sources,
    _quote_front_command,
    _remove_owned_tree,
    _venv_python,
    _verify_generation_release,
    _write_front_wrapper,
    build_host_package,
    build_runtime,
    host_valid_mcp_command,
    probe_runtime,
    schedule_command,
    stage_generation,
    write_generation_configs,
)

updater_check.bind(sys.modules[__name__])
updater_schedule.bind(sys.modules[__name__])
updater_staging.bind(sys.modules[__name__])


def _generation_engine(current: dict) -> str:
    adapter = read_json(Path(current["adapter_config"]))
    if adapter is None or not isinstance(adapter.get("engine_config"), str):
        raise Deferred("current generation adapter config is unreadable")
    return adapter["engine_config"]


def stop_if_idle(adapter: dict, current: dict, deadline: float) -> dict:
    engine = _generation_engine(current)
    stop_budget = min(5, _remaining(deadline, RESERVE))
    previous_handoff = read_status(adapter).get("service_handoff")
    _record(
        adapter,
        service_handoff={
            "status": "pending",
            "error": "interrupted-stop-or-restore-needs-attention",
            "sha": current.get("sha"),
        },
    )
    try:
        output = bounded_run(
            [current["python"], str(HERE / "service_handoff.py"), "stop", engine],
            "", timeout=stop_budget,
        )
    except Deferred:
        raise
    except TimeoutError:
        raise
    except Exception:
        _record(adapter, service_handoff={"status": "failed", "error": "stop-outcome-unconfirmed", "sha": current.get("sha")})
        raise CheckFailed("stop outcome unconfirmed; service needs attention") from None
    try:
        result = json.loads(output.strip())
        if (
            not isinstance(result, dict)
            or type(result.get("idle")) is not bool
            or result.get("service") not in {"absent", "busy", "stopped"}
        ):
            raise ValueError("invalid stop result")
    except (ValueError, TypeError):
        _record(adapter, service_handoff={"status": "failed", "error": "invalid-stop-result", "sha": current.get("sha")})
        raise CheckFailed("stop result unconfirmed; service needs attention") from None
    if result.get("service") != "stopped":
        if isinstance(previous_handoff, dict) and previous_handoff.get("status") in {"failed", "pending"}:
            _clear_status_fields(adapter, "service_handoff")
        else:
            _record(adapter, service_handoff=previous_handoff)
    if result.get("idle") is not True:
        raise Deferred("service has actual active work; switch deferred")
    return result


def _clear_status_fields(adapter, *names):
    status = read_status(adapter)
    if any(status.pop(name, None) is not None for name in names):
        write_status(status, adapter)


def _clear_switch_ghosts(adapter):
    status = read_status(adapter)
    changed = False
    rollback = status.get("rollback")
    if isinstance(rollback, str) and (
        rollback.startswith("native restore FAILED")
        or rollback.startswith("no retained previous host package")
    ):
        status.pop("rollback")
        changed = True
    if "pointer_restore" in status:
        status.pop("pointer_restore")
        changed = True
    if changed:
        write_status(status, adapter)


def _kimi_env(adapter: dict) -> dict | None:
    from paths import resolve_kimi_bin

    try:
        kimi = resolve_kimi_bin(adapter)
    except TimeoutError:
        raise
    except (OSError, ValueError, FileNotFoundError):
        kimi = None
    if isinstance(kimi, str) and kimi:
        return dict(os.environ, MINDIE_KIMI_BIN=kimi)
    return None


def _readback(adapter: dict, deadline: float, reserve: float = RESERVE) -> dict:
    home = adapter.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if not isinstance(home, str) or not home:
        raise CheckFailed("adapter configuration lacks kimi_home for native readback")
    output = bounded_run(
        [sys.executable, str(HERE / "install_kimi_plugin.py"), "--kimi-home", home, "--readback"],
        "", timeout=_op_timeout(deadline, reserve, 60), env=_kimi_env(adapter),
    )
    return json.loads(output)


def _verify_native_record(report: dict, manifest: dict, package: Path) -> None:
    import install_kimi_plugin as native_install_mod

    for surface, server in (manifest.get("mcpServers") or {}).items():
        command = server.get("command")
        if not host_valid_mcp_command(command):
            raise CheckFailed(f"host package mcpServers.{surface}.command is not native-valid: {command!r}")
    data = ((report.get("install") or {}).get("body") or {}).get("data") or {}
    if data.get("id") != manifest["name"] or data.get("hasErrors"):
        raise CheckFailed(f"native install rejected: {json.dumps(data)[:300]}")
    expected = native_install_mod.expected_native_resources(package, manifest)
    problems = native_install_mod.native_record_resource_problems(data, expected, data)
    problems.extend(native_install_mod.native_after_inventory_problems(report, package, manifest, expected))
    if problems:
        raise CheckFailed("native readback mismatch: " + ", ".join(dict.fromkeys(problems)))


def native_install(adapter: dict, package: Path, deadline: float, reserve: float = RESERVE) -> dict:
    home = adapter.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if not isinstance(home, str) or not home:
        raise CheckFailed("adapter configuration lacks kimi_home for native install")
    package = package.resolve()
    manifest = json.loads((package / "kimi.plugin.json").read_text(encoding="utf-8"))
    try:
        output = bounded_run(
            [sys.executable, str(HERE / "install_kimi_plugin.py"), "--kimi-home", home, "--plugin-root", str(package)],
            "", timeout=_op_timeout(deadline, reserve, 120), env=_kimi_env(adapter),
        )
        report = json.loads(output)
    except CheckFailed:
        raise
    except TimeoutError:
        raise
    except Exception as exc:
        try:
            after = _readback(adapter, deadline, reserve)
        except TimeoutError:
            raise
        except Exception as read_exc:
            raise CheckFailed(
                f"native install uncertain ({str(exc)[:200]}); readback also failed ({str(read_exc)[:200]})"
            )
        raise CheckFailed(
            f"native install uncertain ({str(exc)[:200]}); reconciled registry: {json.dumps(after.get('after'))[:300]}"
        )
    _verify_native_record(report, manifest, package)
    return report


def switch(
    adapter: dict, current: dict, generation: Path, python: Path,
    gen_adapter: Path, sha: str, deadline: float,
    idle, install, lock_timeout,
) -> None:
    receipts_dir(adapter).mkdir(parents=True, exist_ok=True)
    atomic_write(
        receipts_dir(adapter) / f"{sha}.json",
        {"sha": sha, "at": time.time(), "previous": current, "generation": str(generation)},
    )
    native_attempted = False
    stopped = False
    final = None
    wait = min(lock_timeout, deadline - RESERVE - time.monotonic())
    if wait <= 0:
        raise LockTimeout("no time left for the exclusive switch lock")
    with OperationLock(adapter).exclusive(timeout=wait):
        try:
            prior = read_json(Path(current['adapter_config']))
            next_adapter = read_json(gen_adapter)
            if ('domain_source_configs' in next_adapter
                    and (prior.get('domain_configs') or {}) != next_adapter['domain_source_configs']):
                raise Deferred('domain routes changed during preparation; switch deferred')
            idle_result = idle(adapter, current, deadline)
            stopped = idle_result.get("service") == "stopped"
            for domain, path in (prior.get('domain_configs') or {}).items():
                if path == prior['engine_config'] or (next_adapter.get('domain_configs') or {}).get(domain) == path:
                    continue
                result = json.loads(bounded_run(
                    [current['python'], str(HERE / 'service_handoff.py'), 'stop', path], '',
                    timeout=_op_timeout(deadline, RESERVE, 5)))
                if result.get('idle') is not True:
                    raise Deferred('domain ' + domain + ' has active work; switch deferred')
            native_attempted = True
            install(adapter, generation / "host-package", deadline)
            final = {
                "generation": str(generation),
                "python": str(python),
                "adapter_config": str(gen_adapter),
                "sha": sha,
            }
            write_current(final, adapter)
            _clear_switch_ghosts(adapter)
        except TimeoutError:
            raise
        except Exception:
            final = None
            pointer_error = None
            try:
                write_current(current, adapter)
            except TimeoutError:
                raise
            except Exception as exc:
                pointer_error = f"pointer restore FAILED: {str(exc)[:200]}"
            native_restored = not native_attempted or _rollback_native(adapter, current, install, deadline)
            if not pointer_error and native_restored:
                final = current
            if pointer_error:
                status = read_status(adapter)
                status["pointer_restore"] = pointer_error
                write_status(status, adapter)
            raise
        finally:
            if stopped:
                _restore_stopped_service(adapter, final, deadline)


def _rollback_native(adapter: dict, previous: dict, install, deadline: float) -> bool:
    package = Path(previous["generation"]) / "host-package"
    if not package.is_dir() and previous.get("sha") is None:
        package = update_dir(adapter) / "bootstrap-package"
    status = read_status(adapter)
    restored = False
    if not package.is_dir():
        status["rollback"] = "no retained previous host package; native registry may still reference the candidate"
    else:
        try:
            prev_adapter = read_json(Path(previous["adapter_config"])) or adapter
            install(prev_adapter, package, deadline, reserve=FEED_BUDGET + HANDOFF_BUDGET)
            restored = True
            status["rollback"] = "previous native package restored with readback"
        except TimeoutError:
            raise
        except Exception as exc:
            status["rollback"] = f"native restore FAILED ({str(exc)[:200]}); previous generation NOT proven restored"
    write_status(status, adapter)
    return restored


def _restore_stopped_service(adapter, final, deadline):
    try:
        if final is None or read_json(current_path(adapter)) != final:
            raise RuntimeError("committed generation or native rollback unproven")
        selected = read_json(Path(final["adapter_config"]))
        if not selected:
            raise RuntimeError("committed adapter is unreadable")
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        env["MINDIE_KIMI_CONFIG"] = final["adapter_config"]
        if selected.get("kimi_home"):
            env["KIMI_CODE_HOME"] = selected["kimi_home"]
        if isinstance(selected.get("kimi_bin"), str) and selected["kimi_bin"]:
            env["MINDIE_KIMI_BIN"] = selected["kimi_bin"]
        if isinstance(selected.get("git_bin"), str) and selected["git_bin"]:
            env["MINDIE_GIT_BIN"] = selected["git_bin"]
        output = bounded_run(
            [final["python"], str(HERE / "service_handoff.py"), "restore", selected["engine_config"]],
            "", env=env, timeout=_op_timeout(deadline, FEED_BUDGET, HANDOFF_BUDGET),
        )
        result = json.loads(output)
        if result.get("status") not in {"restored", "not-needed"}:
            raise RuntimeError("invalid restoration result")
        result["sha"] = final.get("sha")
    except TimeoutError:
        raise
    except Exception as exc:
        result = {"status": "failed", "error": type(exc).__name__, "sha": (final or {}).get("sha")}
    _record(adapter, service_handoff=result)


def _update_diagnostic(stage, exc, revision=None):
    return diagnostic_support.failure(
        "updater", stage, "update_failure", exception=exc, revision=revision, reportable=False
    )


def _record(adapter, **fields) -> dict:
    status = dict(read_status(adapter), at=time.time(), **fields)
    if (status.get("service_handoff") or {}).get("status") in {"failed", "pending"}:
        if status.get("result") in {"current", "switched"}:
            status["result"] = "degraded"
    write_status(status, adapter)
    return status


_RETRYABLE = frozenset({"temporary_network", "rate_limited", "deadline", "resolver", "bad_content"})
_ACTIONABLE = frozenset({"authentication", "permission", "hook_trust", "certificate"})
_STATIC_FAILURE = {
    "temporary_network": "temporary network failure",
    "rate_limited": "rate limited",
    "deadline": "operation deadline exceeded",
    "authentication": "authentication failed",
    "permission": "permission denied",
    "hook_trust": "host hook trust required",
    "certificate": "certificate verification failed",
    "resolver": "dependency resolver conflict",
    "bad_content": "package content rejected",
}


def _retry_delay(count, retry_after=None):
    failures = max(1, int(count))
    delay = min(INTERVAL_SECONDS * (2 ** min(failures - 1, 6)), 6 * 3600)
    delay += random.uniform(0, max(1.0, delay * 0.1))
    if retry_after is not None:
        try:
            delay = max(delay, float(retry_after))
        except (TypeError, ValueError):
            pass
    return delay


def _failure_category(exc):
    category = getattr(exc, "category", None)
    if isinstance(exc, CommandTimedOut) and category == "temporary_network":
        return "deadline"
    return category if category in _RETRYABLE or category in _ACTIONABLE else None


def _failure_history(previous, category):
    history = previous.get("history") if isinstance(previous, dict) else None
    history = list(history) if isinstance(history, list) else []
    history.append({"at": time.time(), "class": category or "unknown"})
    return history[-8:]


def _remember_failure(adapter, sha, exc):
    previous = read_json(failed_path(adapter)) or {}
    if not isinstance(previous, dict) or previous.get("sha") != sha or previous.get("phase"):
        previous = {}
    count = int(previous.get("count") or 0) + 1
    first = previous.get("first_failure_at")
    if not isinstance(first, (int, float)) or isinstance(first, bool):
        first = time.time()
    category = _failure_category(exc)
    error = _STATIC_FAILURE[category] if category else str(exc).replace("\n", " ")[:500]
    record = {
        "sha": sha,
        "at": time.time(),
        "first_failure_at": first,
        "count": count,
        "error": error,
        "history": _failure_history(previous, category),
        "next_retry_at": time.time() + _retry_delay(count, getattr(exc, "retry_after", None)),
    }
    if category:
        record["failure_class"] = category
    atomic_write(failed_path(adapter), record)
    return record


def _clear_active_failure(adapter, sha):
    failed = read_json(failed_path(adapter))
    if not isinstance(failed, dict) or failed.get("sha") != sha or failed.get("phase"):
        return
    cleared = {
        k: failed.get(k)
        for k in ("sha", "at", "first_failure_at", "count", "history")
        if failed.get(k) is not None
    }
    atomic_write(failed_path(adapter), cleared)


def _failure_gate(failed):
    if not isinstance(failed, dict) or failed.get("phase"):
        return None
    due = failed.get("next_retry_at")
    return "wait" if isinstance(due, (int, float)) and not isinstance(due, bool) and due > time.time() else None


def _resolve_is_waiting(adapter, force):
    if force:
        return False
    status = read_status(adapter)
    if not status.get("resolve_wait"):
        return False
    due = status.get("next_retry_at")
    return isinstance(due, (int, float)) and not isinstance(due, bool) and due > time.time()


def _note_resolve_failure(adapter, exc, current_sha):
    status = read_status(adapter)
    count = int(status.get("resolve_failures") or 0) + 1
    first = status.get("resolve_failure_at")
    if not isinstance(first, (int, float)) or isinstance(first, bool):
        first = time.time()
    category = _failure_category(exc)
    error = _STATIC_FAILURE[category] if category else str(exc).replace("\n", " ")[:500]
    result = "action-required" if category in _ACTIONABLE else "check-failed"
    return _record(
        adapter,
        current_sha=current_sha,
        result=result,
        error=error,
        resolve_failures=count,
        resolve_failure_at=first,
        resolve_wait=True,
        resolve_failure_class=category,
        failure_class=category,
        next_retry_at=time.time() + _retry_delay(count, getattr(exc, "retry_after", None) if category else None),
        diagnostic=_update_diagnostic("resolve_release", exc),
    )


def _clear_resolve_wait(adapter):
    status = read_status(adapter)
    changed = False
    seen = status.get("resolve_failures")
    if isinstance(seen, int) and not isinstance(seen, bool) and seen > 0:
        if not isinstance(status.get("resolve_failures_seen"), int) or isinstance(status.get("resolve_failures_seen"), bool):
            status["resolve_failures_seen"] = seen
        status["resolve_failures"] = 0
        changed = True
    for key in ("resolve_wait", "next_retry_at", "resolve_failure_class", "failure_class", "error"):
        if key in status:
            status.pop(key, None)
            changed = True
    if changed:
        write_status(status, adapter)


def status() -> int:
    try:
        adapter = load_adapter_config()
    except FileNotFoundError:
        print(json.dumps({"result": "unconfigured", "hint": "run scripts/setup.py first"}))
        return 0
    base = generations_dir(adapter)
    generations = sorted(
        p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")
    ) if base.is_dir() else []
    print(
        json.dumps(
            {
                "current": read_current(adapter),
                "status": read_status(adapter),
                "failed": read_json(failed_path(adapter)),
                "generations": generations,
            },
            indent=2,
        )
    )
    return 0


def recover() -> int:
    adapter = load_adapter_config()
    failed_path(adapter).unlink(missing_ok=True)
    return check(adapter, force=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("op", choices=["check", "status", "recover", "install-schedule", "uninstall-schedule"])
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.config is not None:
        os.environ["MINDIE_KIMI_CONFIG"] = str(args.config.expanduser().absolute())
    if args.op in {"check", "recover"}:
        from mindie_diagnostics.service_environment import read
        network = update_dir(load_adapter_config()) / "mindie-network.env"
        if network.is_file():
            for key, value in read(network).items():
                os.environ.setdefault(key, value)
    if args.op == "check":
        return check()
    if args.op == "status":
        return status()
    if args.op == "recover":
        return recover()
    if args.op == "install-schedule":
        return install_schedule(args.config)
    return uninstall_schedule()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SystemExit, KeyboardInterrupt):
        raise
    except Exception as exc:
        diagnostic = diagnostic_support.failure("updater", "entry", "update_failure", exception=exc, reportable=False)
        print(json.dumps({"result": "failed", "error_type": type(exc).__name__, "diagnostic": diagnostic}))
        raise SystemExit(1)
