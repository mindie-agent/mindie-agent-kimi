#!/usr/bin/env python3
"""MindIE Kimi automatic updater. One bounded check per invocation.

Driven by the OS scheduler (launchd every 5 minutes on macOS; Windows
task registration code is present but not natively verified). No daemon,
no model, no SessionStart work.

check: serialized by a nonblocking check.lock under one absolute
deadline. Resolve remote main to one SHA -> stage an immutable
generation with its own pinned venv and its own adapter/engine configs
-> build the native host package pointing at a NEW versioned launcher
path (live entrypoints are never mutated by staging) -> under the
exclusive operation lock call core stop_if_idle through the CURRENT
committed interpreter -> install through Kimi's native plugin API with
readback -> atomically flip current.json (one committed tuple). Rollback
restores the previous pointer AND the previous native package/readback.
Feed sync is independent: it runs even when the candidate fails.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
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
        engine_config_path,
        load_adapter_config,
        load_engine_config,
    )
except ImportError as exc:
    diagnostic_support.failure(
        "updater", "bootstrap_import", "missing_committed_file", exception=exc)
    raise SystemExit("committed updater dependency is unavailable") from None

DEFAULT_REMOTE = "https://github.com/mindie-agent/mindie-agent-kimi.git"
INTERVAL_SECONDS = 300
CHECK_BUDGET = 240.0
# Reserved tail of the check budget: native rollback and feed sync always
# get their own opportunity, even after failed preparation. Preparation
# steps must leave this much; total never exceeds CHECK_BUDGET (< 300s).
ROLLBACK_BUDGET = 45.0
FEED_BUDGET = 60.0
HANDOFF_BUDGET = 8.0
RESERVE = ROLLBACK_BUDGET + FEED_BUDGET + HANDOFF_BUDGET
SHA = r"[0-9a-f]{40}"
COMPLETE = ".mindie-generation-complete"
LAUNCHER = "mindie_launch.py"
MIN_PYTHON = (3, 11)
GIT_ENV = dict(os.environ, GIT_TERMINAL_PROMPT="0")
PIP_ENV = dict(
    os.environ,
    PIP_RETRIES="0",
    PIP_NO_INPUT="1",
    PIP_DISABLE_PIP_VERSION_CHECK="1",
)

_FEED_OK = frozenset({"synced", "unchanged"})
_FEED_PENDING = frozenset({"busy", "deferred"})
_FEED_ERROR = frozenset({"unavailable", "invalid", "exhausted"})
_FEED_KNOWN = _FEED_OK | _FEED_PENDING | _FEED_ERROR


def fold_feed_results(output):
    """Fold core `sync` stdout, which MUST be one JSON list (no prefix/suffix).

    Empty list is ok. busy/deferred means not completed now, not a failed
    attempt: all-pending folds to deferred. Mix of completed (synced/unchanged)
    with pending or failed folds to degraded. No completed rows plus at least
    one unavailable/invalid/exhausted folds to sync_failed. Every original
    row is preserved. Unknown status or malformed stdout raises.
    """
    text = (output or "").strip()
    if not text:
        raise ValueError("knowledge sync returned empty output")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("knowledge sync returned non-JSON") from exc
    if not isinstance(payload, list):
        raise ValueError("knowledge sync result is not a list")
    rows = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("knowledge sync row is not an object")
        status = item.get("status")
        repo = item.get("repository")
        if not isinstance(status, str) or not status:
            raise ValueError("knowledge sync row missing status")
        if not isinstance(repo, str) or not repo:
            raise ValueError("knowledge sync row missing repository")
        if status not in _FEED_KNOWN:
            raise ValueError("knowledge sync unknown status: " + status)
        rows.append(item)
    good = sum(1 for row in rows if row["status"] in _FEED_OK)
    pending = sum(1 for row in rows if row["status"] in _FEED_PENDING)
    failed = sum(1 for row in rows if row["status"] in _FEED_ERROR)
    if not rows or good == len(rows):
        aggregate = "ok"
        summary = None
    elif good and (pending or failed):
        aggregate = "degraded"
        summary = _feed_error_summary(rows)
    elif failed:
        aggregate = "sync_failed"
        summary = _feed_error_summary(rows)
    elif pending:
        aggregate = "deferred"
        summary = _feed_error_summary(rows)
    else:
        aggregate = "sync_failed"
        summary = _feed_error_summary(rows)
    return aggregate, rows, summary


def _feed_error_summary(rows):
    parts = []
    for row in rows:
        if row["status"] in _FEED_OK:
            continue
        detail = row.get("detail") or row.get("cause") or ""
        parts.append(f"{row['repository']}:{row['status']}:{str(detail)[:80]}")
    return "; ".join(parts)[:240]


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
    """Finite operation window ending at deadline-reserve. No positive
    floor after expiry: reject instead of creating new time."""
    left = deadline - reserve - time.monotonic()
    if left <= 0:
        raise CheckFailed("no time left in this operation's reserved window")
    return min(cap, left)


def _git(args, *, timeout, deadline, cwd=None):
    return bounded_run(["git", *args], "",
                       timeout=min(timeout, _remaining(deadline, RESERVE)),
                       cwd=cwd, env=GIT_ENV)


def resolve_main(remote: str, deadline: float) -> str:
    output = _git(["ls-remote", remote, "refs/heads/main"], timeout=30,
                  deadline=deadline)
    match = re.search(rf"\b({SHA})\s+refs/heads/main", output)
    if not match:
        raise CheckFailed("remote main did not resolve to a commit SHA")
    return match.group(1)


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def build_runtime(generation: Path, deadline: float) -> Path:
    """Real pinned venv from the generation's runtime-requirements.txt.
    Built at the FINAL generation path so venv paths survive."""
    if sys.version_info < MIN_PYTHON:
        raise CheckFailed(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required")
    requirements = generation / "runtime-requirements.txt"
    text = requirements.read_text()
    if re.search(r"@main\b", text):
        raise CheckFailed("runtime-requirements.txt must pin full commit SHAs, not @main")
    venv = generation / ".venv"
    python = _venv_python(venv)
    bounded_run([sys.executable, "-m", "venv", str(venv)], "",
                timeout=min(180, _remaining(deadline, RESERVE)))
    bounded_run(
        [str(python), "-m", "pip", "install", "-r", str(requirements)],
        "",
        timeout=min(600, _remaining(deadline, RESERVE)),
        max_output=512 * 1024,
        env=PIP_ENV,
    )
    return python


def probe_runtime(python: Path, deadline: float, generation: Path) -> None:
    from setup import probe_script

    parser = str(generation / "scripts" / "transcript.py")
    output = bounded_run([str(python), "-c", probe_script(parser)], "",
                         timeout=min(60, _remaining(deadline, RESERVE)))
    if not output.strip().endswith("OK"):
        raise CheckFailed(f"generation runtime probe failed: {output.strip()[:300]}")


def write_generation_configs(generation: Path, python: Path, adapter: dict) -> Path:
    """Per-generation adapter+engine JSON. Admission/community/first-use
    state paths stay stable; parser/organizer/interpreter move with the
    generation. Returns the generation adapter config path."""
    engine = load_engine_config(adapter)
    config_dir = generation / "config"
    gen_engine = config_dir / "kimi.engine.json"
    gen_adapter = config_dir / "kimi.adapter.json"
    atomic_write(gen_engine, dict(
        engine,
        transcript_adapter=str(generation / "scripts" / "transcript.py"),
        agent_command=[str(python), str(generation / "scripts" / "organizer.py")],
    ))
    atomic_write(gen_adapter, dict(
        adapter,
        python=str(python),
        engine_config=str(gen_engine),
    ))
    return gen_adapter


def host_valid_mcp_command(command: str) -> bool:
    """Native Kimi accepts a PATH token (no slash) or a package-relative
    path that starts with './'. Absolute sys.executable is rejected and
    the server is skipped with a warning (mcpServerCount stays 0)."""
    if not isinstance(command, str) or not command:
        return False
    if command.startswith("./"):
        return True
    if os.path.isabs(command) or "/" in command or "\\" in command:
        return False
    return True


def _write_front_wrapper(package: Path, python: Path) -> str:
    """Package-relative wrapper that execs the retained front interpreter.

    Native command lookup cannot use an absolute interpreter path; the
    wrapper stays inside the copied plugin root and starts with './'.
    """
    if os.name == "nt":
        name = "mindie-front.cmd"
        (package / name).write_text(f'@echo off\r\n"{python}" %*\r\n')
    else:
        name = "mindie-front"
        (package / name).write_text(
            f"#!/bin/sh\nexec {shlex.quote(str(python))} \"$@\"\n"
        )
        (package / name).chmod(0o755)
    return f"./{name}"


def _quote_front_command(python, launcher, config_file) -> str:
    parts = [str(python), str(launcher), "--config", str(config_file)]
    if os.name == "nt":
        return " ".join(f'"{part}"' for part in parts)
    return " ".join(shlex.quote(part) for part in parts)


def schedule_command(launcher: Path, config_file: Path) -> list[str]:
    return [sys.executable, str(launcher), "--config", str(config_file),
            "updater", "check"]


def build_host_package(generation: Path, adapter: dict, sha: str,
                       package_dir: Path | None = None,
                       config_file=None) -> Path:
    """Native host package: manifest + Skills + commands. The manifest
    points at a NEW versioned launcher path for this exact revision,
    with bounded.py copied next to it (the front imports it); live
    launchers are never touched during staging. manifest.version is
    stamped uniquely with the candidate commit."""
    if config_file is None:
        raise ValueError("build_host_package requires an explicit config_file")
    config_file = Path(config_file)
    launcher_dir = launch_dir(adapter) / sha
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher = launcher_dir / LAUNCHER
    if not launcher.is_file():
        for name in (LAUNCHER, "bounded.py", "diagnostic_support.py", "diagnostic_fallback.py"):
            shutil.copy2(generation / "scripts" / name, launcher_dir / name)
    package = package_dir if package_dir is not None else generation / "host-package"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    manifest = json.loads((generation / "kimi.plugin.json").read_text())
    base = str(manifest.get("version") or "0.1.0").split("+")[0]
    manifest["version"] = f"{base}+mindie.{sha[:12]}"
    base_python = sys.executable
    front = _write_front_wrapper(package, Path(base_python))
    bound = ["--config", str(config_file)]
    for surface in ("knowledge", "remote"):
        manifest["mcpServers"][surface] = {
            "command": front,
            "args": [str(launcher), *bound, "mcp", surface],
        }
    quoted = _quote_front_command(base_python, launcher, config_file)
    for hook in manifest.get("hooks", []):
        op = {"PreToolUse": "pretool", "Stop": "stop"}.get(hook.get("event"))
        if op:
            hook["command"] = f"{quoted} hook {op}"
    for name in ("skills", "commands"):
        source = generation / name
        if source.is_dir():
            shutil.copytree(source, package / name)
    (package / "kimi.plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    metadata = {"version": manifest["version"]}
    if re.fullmatch(SHA, sha):
        metadata["revision"] = sha
    for folder in (generation / "scripts", launcher_dir, package / "scripts"):
        path = folder / "diagnostic-build.json"
        if not path.exists():
            atomic_write(path, metadata)
    return package


def stage_generation(sha: str, remote: str, adapter: dict, deadline: float,
                     build=build_runtime) -> tuple[Path, Path, Path]:
    """Returns (generation, venv python, generation adapter config)."""
    target = generations_dir(adapter) / sha
    if (target / COMPLETE).is_file():
        python = _venv_python(target / ".venv")
        gen_adapter = target / "config" / "kimi.adapter.json"
        if python.exists() and gen_adapter.is_file():
            return target, python, gen_adapter
        shutil.rmtree(target)
    elif target.exists():
        shutil.rmtree(target)
    staging = generations_dir(adapter) / f".staging-{sha}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _git(["init", "-q", str(staging)], timeout=30, deadline=deadline)
        _git(["remote", "add", "origin", remote], timeout=15,
             deadline=deadline, cwd=staging)
        _git(["fetch", "--depth", "1", "origin", sha], timeout=180,
             deadline=deadline, cwd=staging)
        _git(["checkout", "-q", "--detach", "FETCH_HEAD"], timeout=60,
             deadline=deadline, cwd=staging)
        os.replace(staging, target)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        python = build(target, deadline)
        probe_runtime(python, deadline, target)
        gen_adapter = write_generation_configs(target, python, adapter)
        build_host_package(target, adapter, sha, config_file=gen_adapter)
        (target / COMPLETE).write_text(f"{sha}\n")
        return target, python, gen_adapter
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _generation_engine(current: dict) -> str:
    adapter = read_json(Path(current["adapter_config"]))
    if adapter is None or not isinstance(adapter.get("engine_config"), str):
        raise Deferred("current generation adapter config is unreadable")
    return adapter["engine_config"]


def stop_if_idle(adapter: dict, current: dict, deadline: float) -> dict:
    """Core authenticated stop_if_idle through the CURRENT committed
    interpreter. Fails CLOSED on import/RPC/API problems; a truly absent
    service endpoint is idle. Unknown/pending receipts and idle grants do
    not block; only actual active work does."""
    engine = _generation_engine(current)
    stop_budget = min(5, _remaining(deadline, RESERVE))
    previous_handoff = read_status(adapter).get("service_handoff")
    _record(adapter, service_handoff={"status": "pending",
            "error": "interrupted-stop-or-restore-needs-attention",
            "sha": current.get("sha")})
    try:
        output = bounded_run(
            [current["python"], str(HERE / "service_handoff.py"), "stop", engine],
            "",
            timeout=stop_budget,
        )
    except Deferred:
        raise
    except Exception as exc:
        _record(adapter, service_handoff={"status": "failed",
                "error": "stop-outcome-unconfirmed", "sha": current.get("sha")})
        raise CheckFailed("stop outcome unconfirmed; service needs attention") from None
    try:
        result = json.loads(output.strip())
        if (not isinstance(result, dict)
                or type(result.get("idle")) is not bool
                or result.get("service") not in {"absent", "busy", "stopped"}):
            raise ValueError("invalid stop result")
    except (ValueError, TypeError):
        _record(adapter, service_handoff={"status": "failed",
                "error": "invalid-stop-result", "sha": current.get("sha")})
        raise CheckFailed("stop result unconfirmed; service needs attention") from None
    if result.get("service") != "stopped":
        _record(adapter, service_handoff=previous_handoff)
    if result.get("idle") is not True:
        raise Deferred("service has actual active work; switch deferred")
    return result


def _readback(adapter: dict, deadline: float, reserve: float = RESERVE) -> dict:
    home = adapter.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if not isinstance(home, str) or not home:
        raise CheckFailed("adapter configuration lacks kimi_home for native readback")
    output = bounded_run(
        [sys.executable, str(HERE / "install_kimi_plugin.py"),
         "--kimi-home", home, "--readback"],
        "",
        timeout=_op_timeout(deadline, reserve, 60),
    )
    return json.loads(output)


def _verify_native_record(report: dict, manifest: dict, package: Path) -> None:
    """Exact selected plugin record: id, stamped version, enabled/error
    state, originalSource, and actually-loaded MCP/hook/command resources.

    enabled=true / state=ok / hasErrors=false is not success when native
    skipped MCP servers (warning diagnostics, mcpServerCount=0)."""
    import install_kimi_plugin as native_install_mod

    for surface, server in (manifest.get("mcpServers") or {}).items():
        command = server.get("command")
        if not host_valid_mcp_command(command):
            raise CheckFailed(
                f"host package mcpServers.{surface}.command is not native-valid: "
                f"{command!r}"
            )
    install = (report.get("install") or {}).get("body") or {}
    data = install.get("data") or {}
    if data.get("id") != manifest["name"] or data.get("hasErrors"):
        raise CheckFailed(f"native install rejected: {json.dumps(data)[:300]}")
    expected = native_install_mod.expected_native_resources(package, manifest)
    problems = native_install_mod.native_record_resource_problems(data, expected, data)
    problems.extend(
        native_install_mod.native_after_inventory_problems(
            report, package, manifest, expected))
    if problems:
        raise CheckFailed("native readback mismatch: " + ", ".join(dict.fromkeys(problems)))


def native_install(adapter: dict, package: Path, deadline: float,
                   reserve: float = RESERVE) -> dict:
    home = adapter.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if not isinstance(home, str) or not home:
        raise CheckFailed("adapter configuration lacks kimi_home for native install")
    package = package.resolve()
    manifest = json.loads((package / "kimi.plugin.json").read_text())
    try:
        output = bounded_run(
            [sys.executable, str(HERE / "install_kimi_plugin.py"),
             "--kimi-home", home, "--plugin-root", str(package)],
            "",
            timeout=_op_timeout(deadline, reserve, 120),
        )
        report = json.loads(output)
    except CheckFailed:
        raise
    except Exception as exc:
        # Uncertain outcome: reconcile the actual native registry before
        # declaring anything, inside the SAME reserved window.
        try:
            after = _readback(adapter, deadline, reserve)
        except Exception as read_exc:
            raise CheckFailed(
                f"native install uncertain ({str(exc)[:200]}); readback also "
                f"failed ({str(read_exc)[:200]})"
            )
        raise CheckFailed(
            f"native install uncertain ({str(exc)[:200]}); reconciled "
            f"registry: {json.dumps(after.get('after'))[:300]}"
        )
    _verify_native_record(report, manifest, package)
    return report


def switch(adapter: dict, current: dict, generation: Path, python: Path,
           gen_adapter: Path, sha: str, deadline: float,
           idle, install, lock_timeout) -> None:
    receipts_dir(adapter).mkdir(parents=True, exist_ok=True)
    atomic_write(receipts_dir(adapter) / f"{sha}.json", {
        "sha": sha,
        "at": time.time(),
        "previous": current,
        "generation": str(generation),
    })
    native_attempted = False
    stopped = False
    final = None
    # Lock wait must fit the same cutoff (before the reserved tail).
    wait = min(lock_timeout, deadline - RESERVE - time.monotonic())
    if wait <= 0:
        raise LockTimeout("no time left for the exclusive switch lock")
    with OperationLock(adapter).exclusive(timeout=wait):
        try:
            # Idle/deferred failures before any install attempt leave
            # native state untouched: nothing is reinstalled.
            idle_result = idle(adapter, current, deadline)
            stopped = idle_result.get("service") == "stopped"
            native_attempted = True
            install(adapter, generation / "host-package", deadline)
            # Single atomic pointer flip: one committed tuple.
            final = {
                "generation": str(generation),
                "python": str(python),
                "adapter_config": str(gen_adapter),
                "sha": sha,
            }
            write_current(final, adapter)
        except Exception:
            # Install, pointer flip AND rollback stay under the SAME
            # exclusive lock. Restore the previous committed tuple
            # deterministically (idempotent if the flip never landed);
            # an install attempt may have mutated native state even when
            # it raised: reconcile/restore it afterwards.
            final = None
            pointer_error = None
            try:
                write_current(current, adapter)
            except Exception as exc:
                pointer_error = f"pointer restore FAILED: {str(exc)[:200]}"
            native_restored = not native_attempted or _rollback_native(
                adapter, current, install, deadline)
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


def _rollback_native(adapter: dict, previous: dict, install,
                     deadline: float) -> bool:
    """Best-effort native restore of the previous generation's retained
    host package, inside the rollback reserve. Honest status only: a
    failed restore is never claimed as success."""
    package = Path(previous["generation"]) / "host-package"
    if not package.is_dir() and previous.get("sha") is None:
        package = update_dir(adapter) / "bootstrap-package"
    status = read_status(adapter)
    restored = False
    if not package.is_dir():
        status["rollback"] = (
            "no retained previous host package; native registry may still "
            "reference the candidate"
        )
    else:
        try:
            prev_adapter = read_json(Path(previous["adapter_config"])) or adapter
            # Rollback window ends where the feed reserve begins: the two
            # reserves never overlap.
            install(prev_adapter, package, deadline, reserve=FEED_BUDGET + HANDOFF_BUDGET)
            restored = True
            status["rollback"] = "previous native package restored with readback"
        except Exception as exc:
            status["rollback"] = (
                f"native restore FAILED ({str(exc)[:200]}); previous "
                "generation NOT proven restored"
            )
    write_status(status, adapter)
    return restored


def _restore_stopped_service(adapter, final, deadline):
    """One attempt, only after our stop; never resolve a bootstrap fallback."""
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
        output = bounded_run(
            [final["python"], str(HERE / "service_handoff.py"),
             "restore", selected["engine_config"]], "", env=env,
            timeout=_op_timeout(deadline, FEED_BUDGET, HANDOFF_BUDGET))
        result = json.loads(output)
        if result.get("status") not in {"restored", "not-needed"}:
            raise RuntimeError("invalid restoration result")
        result["sha"] = final.get("sha")
    except Exception as exc:
        result = {"status": "failed", "error": type(exc).__name__,
                  "sha": (final or {}).get("sha")}
    _record(adapter, service_handoff=result)


def _update_diagnostic(stage, exc, revision=None):
    """Local-only update boundary record.

    Fetch, build, and native state may be external, configuration, or
    credentials. This is not a confirmed product bug, so it is not
    reportable. The lower layer already owns reportable errors.
    """
    return diagnostic_support.failure(
        "updater",
        stage,
        "update_failure",
        exception=exc,
        revision=revision,
        reportable=False,
    )


def _record(adapter, **fields) -> dict:
    status = dict(read_status(adapter), at=time.time(), **fields)
    if (status.get("service_handoff") or {}).get("status") in {"failed", "pending"}:
        if status.get("result") in {"current", "switched"}:
            status["result"] = "degraded"
    write_status(status, adapter)
    return status


def _check_once(adapter: dict, deadline: float, *, force, build, idle,
                install, lock_timeout) -> int:
    current = read_current(adapter)
    remote = (os.environ.get("MINDIE_KIMI_UPDATE_REMOTE")
              or adapter.get("update_remote") or DEFAULT_REMOTE)
    try:
        sha = resolve_main(remote, deadline)
    except Exception as exc:
        diagnostic = _update_diagnostic("resolve_main", exc)
        _record(adapter, current_sha=current.get("sha"), result="check-failed",
                error=str(exc)[:500], diagnostic=diagnostic)
        print(json.dumps({"result": "check-failed", "error": str(exc)[:300],
                          "diagnostic": diagnostic}))
        return 1
    failed = read_json(failed_path(adapter))
    if not force and failed and failed.get("sha") == sha \
            and current.get("sha") != sha:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="suppressed-known-failed", error=failed.get("error"))
        print(json.dumps({"result": "suppressed-known-failed", "sha": sha}))
        return 0
    if sha == current.get("sha"):
        status = _record(adapter, current_sha=sha, result="current")
        print(json.dumps(status))
        return int(status["result"] == "degraded")
    try:
        generation, python, gen_adapter = stage_generation(
            sha, remote, adapter, deadline, build=build)
    except Exception as exc:
        diagnostic = _update_diagnostic("stage_generation", exc)
        atomic_write(failed_path(adapter),
                     {"sha": sha, "error": str(exc)[:500], "at": time.time()})
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="failed", error=str(exc)[:500], diagnostic=diagnostic)
        print(json.dumps({"result": "failed", "error": str(exc)[:500],
                          "diagnostic": diagnostic}))
        return 1
    try:
        switch(adapter, current, generation, python, gen_adapter, sha,
               deadline, idle, install, lock_timeout)
    except LockTimeout:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="deferred-busy", error="operation lock stayed shared")
        print(json.dumps({"result": "deferred-busy", "sha": sha}))
        return 0
    except Deferred as exc:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="deferred", error=str(exc)[:500])
        print(json.dumps({"result": "deferred", "sha": sha,
                          "reason": str(exc)[:300]}))
        return 0
    except Exception as exc:
        diagnostic = _update_diagnostic("switch", exc)
        atomic_write(failed_path(adapter),
                     {"sha": sha, "error": str(exc)[:500], "at": time.time()})
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="failed", error=str(exc)[:500], diagnostic=diagnostic)
        print(json.dumps({"result": "failed", "error": str(exc)[:500],
                          "diagnostic": diagnostic}))
        return 1
    status = _record(adapter, current_sha=sha, result="switched", needs_host_reload=True,
            note=("MCP/hook dispatch uses the new generation per call; "
                  "existing task authorization is preserved. Native "
                  "Skill/command/MCP/hook definitions refresh with the Kimi host."))
    print(json.dumps(status))
    return int(status["result"] == "degraded")


def check(config=None, *, force=False, build=build_runtime,
          idle=stop_if_idle, install=native_install, lock_timeout=30.0) -> int:
    adapter = config if isinstance(config, dict) else load_adapter_config()
    update_dir(adapter).mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + CHECK_BUDGET
    try:
        with check_lock(adapter):
            code = _check_once(adapter, deadline, force=force, build=build,
                               idle=idle, install=install,
                               lock_timeout=lock_timeout)
            # Feed sync is covered by the SAME check lock and always gets
            # its reserved opportunity, even after failed preparation.
            _feed_sync(adapter, deadline)
            try:
                _logging_maintenance(adapter, deadline)
            except Exception as exc:
                # Retention/storage failures must not replace the update outcome.
                _update_diagnostic("logging_maintenance", exc)
            return code
    except LockTimeout:
        print(json.dumps({"result": "check-already-running"}))
        return 0


def _logging_maintenance(adapter, deadline):
    """Offline diagnostic retention under the existing check lock.

    Selects the committed interpreter and runs reporting maintain with
    --update-running: a bounded handoff of an ALREADY enabled and
    healthy-running reporter. It never starts a disabled, absent, or
    crashed service, never ensures the service, never retries, and never
    changes the updater return code. Shared reporting may be disabled. The
    actual JSON result (including aggregate status=degraded from a failed
    or conflicting handoff) is exposed via check=False: a nonzero exit
    with a JSON result is parsed normally, never silently discarded.
    """
    status = read_status(adapter)
    remaining = deadline - time.monotonic()
    if remaining <= 0.1:
        status["logging_maintenance"] = {"status": "skipped_budget"}
        write_status(status, adapter)
        return
    try:
        with OperationLock(adapter).shared(timeout=min(0.5, remaining)):
            current = read_current(adapter)
        # Pass the REAL remaining window: the CLI's 75s default includes
        # offline work and skips the upgrade unless a full 60s handoff plus
        # 1s exit remains; 2s is this parent's exit/startup margin.
        available = min(75, deadline - time.monotonic())
        if available <= 0:
            status["logging_maintenance"] = {"status": "skipped_budget"}
            write_status(status, adapter)
            return
        output = bounded_run(
            [current["python"], "-m", "mindie_diagnostics.cli",
             "reporting", "maintain", "--update-running", "--config",
             str(diagnostic_support.reporting_config_path()),
             "--budget-seconds", str(max(0, available - 2))],
            "",
            timeout=available,
            max_output=65536,
            check=False,
        )
        payload = json.loads((output or "").strip() or "null")
        if not isinstance(payload, dict):
            raise ValueError("maintenance result is not an object")
        status["logging_maintenance"] = payload
    except LockTimeout:
        status["logging_maintenance"] = {"status": "deferred"}
    except Exception as exc:
        status["logging_maintenance"] = {
            "status": "unavailable",
            "type": type(exc).__name__,
        }
    write_status(status, adapter)


def _feed_sync(adapter: dict, deadline: float) -> bool:
    """Independent knowledge feed sync through shared core, with the
    CURRENT committed interpreter. Tuple selection happens INSIDE the
    shared operation lock. No organizer, no model; bounded by the feed
    reserve; never retried. Returns True only when every feed is
    synced/unchanged (or the configured list is empty)."""
    status = read_status(adapter)
    left = deadline - time.monotonic()
    if left <= 0:
        status["feed_sync"] = "skipped: check budget exhausted"
        status["feed_error"] = "skipped: check budget exhausted"
        status.pop("feed_results", None)
        write_status(status, adapter)
        return False
    try:
        with OperationLock(adapter).shared(timeout=min(5, left)):
            current = read_current(adapter)
            engine = _generation_engine(current)
            output = bounded_run(
                [current["python"], "-m", "mindie_knowledge.loop.cli",
                 "sync", "--config", engine],
                "",
                timeout=min(FEED_BUDGET, deadline - time.monotonic()),
            )
        aggregate, rows, summary = fold_feed_results(output)
        status["feed_sync"] = aggregate
        status["feed_results"] = rows
        if summary:
            status["feed_error"] = summary
        else:
            status.pop("feed_error", None)
        write_status(status, adapter)
        return aggregate == "ok"
    except Exception as exc:
        status["feed_sync"] = "sync_failed"
        status["feed_error"] = str(exc)[:240]
        status.pop("feed_results", None)
        write_status(status, adapter)
        return False


def status() -> int:
    try:
        adapter = load_adapter_config()
    except FileNotFoundError:
        print(json.dumps({"result": "unconfigured",
                          "hint": "run scripts/setup.py first"}))
        return 0
    base = generations_dir(adapter)
    generations = sorted(
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ) if base.is_dir() else []
    print(json.dumps({
        "current": read_current(adapter),
        "status": read_status(adapter),
        "failed": read_json(failed_path(adapter)),
        "generations": generations,
    }, indent=2))
    return 0


def recover() -> int:
    """Explicit recovery: clear the failed-revision suppression, re-check."""
    adapter = load_adapter_config()
    failed = failed_path(adapter)
    if failed.exists():
        failed.unlink()
    return check(adapter, force=True)


def install_schedule(config_file=None) -> int:
    if config_file is not None:
        config_file = Path(config_file).expanduser().absolute()
        adapter = json.loads(config_file.read_text())
        if not isinstance(adapter, dict):
            raise SystemExit("adapter configuration must be one JSON object")
    else:
        adapter = load_adapter_config()
        config_file = config_path()
    current = read_current(adapter)
    launcher = launch_dir(adapter) / (current.get("sha") or "bootstrap") / LAUNCHER
    if not launcher.is_file() or not (launcher.parent / "bounded.py").is_file():
        raise SystemExit(f"no launcher available at {launcher.parent}")
    command = schedule_command(launcher, config_file)
    if sys.platform == "darwin":
        import plistlib

        plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as stream:
            plistlib.dump({
                "Label": "agent.mindie.kimi-update",
                "ProgramArguments": command,
                "StartInterval": INTERVAL_SECONDS,
                "StandardOutPath": os.devnull,
                "StandardErrorPath": os.devnull,
            }, stream)
        uid = os.getuid()
        try:
            bounded_run(["launchctl", "bootout", f"gui/{uid}", str(plist)],
                        "", timeout=15)
        except RuntimeError:
            pass
        bounded_run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)],
                    "", timeout=15)
        print(json.dumps({"result": "scheduled", "agent": str(plist),
                          "interval_seconds": INTERVAL_SECONDS}))
        return 0
    if os.name == "nt":
        # Windows registration: explicit but NOT natively verified.
        task = " ".join(f'"{item}"' for item in command)
        bounded_run(
            ["schtasks", "/Create", "/F", "/TN", "MindIEKimiUpdate",
             "/SC", "MINUTE", "/MO", "5", "/TR", task],
            "",
            timeout=30,
        )
        print(json.dumps({"result": "scheduled", "task": "MindIEKimiUpdate",
                          "verified": False}))
        return 0
    print(json.dumps({
        "result": "unsupported-platform",
        "hint": f"run every {INTERVAL_SECONDS}s via your scheduler: "
                + " ".join(command),
    }))
    return 1


def _native_run(argv, timeout):
    """One bounded native scheduler control command with a KNOWN return code.

    bounded.run returns stdout only and discards returncode/stderr, but
    scheduler truth needs them: launchctl print exit 113 is positive
    missing-service evidence while any other failure is not absence. These
    are fixed small-output OS control commands (launchctl/PowerShell), so a
    plain bounded subprocess.run suffices; callers cap any retained
    diagnostic text. Returns (returncode, stdout, stderr); raises
    subprocess.TimeoutExpired past the bounded deadline and OSError when
    the manager executable itself is unavailable.
    """
    completed = subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        errors="replace", timeout=timeout)
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _launchd_state(label, uid, timeout=10):
    """Actual launchd state for the exact service target.

    "absent" ONLY on positive missing-service evidence (launchctl print
    exit 113); permission, manager, and parse failures are "unknown",
    never absence.
    """
    try:
        code, _, stderr = _native_run(
            ["launchctl", "print", f"gui/{uid}/{label}"], timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"[:240]
    if code == 0:
        return "loaded", ""
    if code == 113:
        return "absent", ""
    return "unknown", (stderr or "").strip()[:240] or f"launchctl print exited {code}"


def _task_state(task, timeout=30):
    """Exact scheduled-task state via structured PowerShell enumeration.

    ErrorAction Stop makes a manager error exit nonzero; a successful
    enumeration with zero exact TaskPath/TaskName matches proves absence.
    Localized arbitrary error text is never treated as absence.
    """
    escaped = task.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$m = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { "
        f"$_.TaskName -eq '{escaped}' -and $_.TaskPath -eq '\\' }}); "
        "Write-Output $m.Count"
    )
    try:
        code, stdout, stderr = _native_run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"[:240]
    if code != 0:
        return "unknown", (stderr or "").strip()[:240] or f"powershell exited {code}"
    try:
        count = int((stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "unknown", "unparseable task enumeration"
    return ("present" if count else "absent"), ""


def _schedule_failed(stage, category, *, plist=None, task=None, detail=""):
    payload = {"result": "failed", "stage": stage, "category": category}
    if plist is not None:
        payload["retained_plist"] = str(plist)
    if task is not None:
        payload["retained_task"] = task
    if detail:
        payload["error"] = str(detail)[:240]
    print(json.dumps(payload))
    return 1


def _uninstall_launchd(label, plist):
    """Truthful launchd removal of the exact owned service target.

    Query before any mutation; one bootout by service target (a missing
    plist with a still-loaded label is still a service); bounded absence
    readback, never a second mutation; the plist is unlinked only after
    absence is established. Uncertain bootout failure can still end in
    success when the readback proves absence.
    """
    uid = os.getuid()
    target = f"gui/{uid}/{label}"
    state, detail = _launchd_state(label, uid)
    if state == "unknown":
        return _schedule_failed("query", "manager-or-permission",
                                plist=plist, detail=detail)
    if state == "loaded":
        bootout_detail = ""
        try:
            code, _, stderr = _native_run(["launchctl", "bootout", target], 15)
            if code:
                bootout_detail = (stderr or "").strip()[:240]
        except (OSError, subprocess.TimeoutExpired) as exc:
            bootout_detail = f"{type(exc).__name__}: {exc}"[:240]
        cutoff = time.monotonic() + 3.0
        while state != "absent" and time.monotonic() < cutoff:
            time.sleep(0.2)
            left = cutoff - time.monotonic()
            if left <= 0:
                break
            state, detail = _launchd_state(label, uid, timeout=min(5.0, left))
        if state != "absent":
            return _schedule_failed(
                "readback",
                "service-retained" if state == "loaded" else "state-unproven",
                plist=plist, detail=detail or bootout_detail)
    plist.unlink(missing_ok=True)
    print(json.dumps({"result": "unscheduled"}))
    return 0


def _uninstall_task(task):
    """Truthful scheduled-task removal: structured query, one unregister,
    then one bounded absence readback. Manager errors fail; they are never
    claimed as absence."""
    state, detail = _task_state(task)
    if state == "unknown":
        return _schedule_failed("query", "manager-or-permission",
                                task=task, detail=detail)
    if state == "present":
        escaped = task.replace("'", "''")
        script = (f"Unregister-ScheduledTask -TaskName '{escaped}' "
                  "-TaskPath '\\' -Confirm:$false -ErrorAction Stop")
        try:
            code, _, stderr = _native_run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                30)
            if code:
                detail = (stderr or "").strip()[:240]
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"{type(exc).__name__}: {exc}"[:240]
        cutoff = time.monotonic() + 3.0
        while state != "absent" and time.monotonic() < cutoff:
            time.sleep(0.2)
            left = cutoff - time.monotonic()
            if left <= 0:
                break
            state, query_detail = _task_state(task, timeout=min(10.0, left))
            if state != "present":
                detail = query_detail or detail
        if state != "absent":
            return _schedule_failed(
                "readback",
                "service-retained" if state == "present" else "state-unproven",
                task=task, detail=detail)
    print(json.dumps({"result": "unscheduled"}))
    return 0


def uninstall_schedule(*, label=None, plist_path=None, schedule_root=None,
                       task=None) -> int:
    """Remove ONLY this adapter's update schedule, truthfully.

    Defaults are the current label/path; the keyword-only explicit
    label/plist-path/schedule-root/task exist for isolated native
    acceptance. Never touches the diagnostics/shared reporter or the other
    adapter's scheduler. Only positively identified absence is idempotent
    success; unproven state returns nonzero JSON and retains the plist and
    runnable artifacts.
    """
    if sys.platform == "darwin":
        label = label or "agent.mindie.kimi-update"
        if plist_path is not None:
            plist = Path(plist_path).expanduser().absolute()
        else:
            root = (Path(schedule_root).expanduser().absolute() if schedule_root
                    else Path.home() / "Library" / "LaunchAgents")
            plist = root / (label + ".plist")
        return _uninstall_launchd(label, plist)
    if os.name == "nt":
        # Windows removal: explicit but NOT natively verified.
        return _uninstall_task(task or "MindIEKimiUpdate")
    print(json.dumps({
        "result": "unsupported-platform",
        "hint": "remove the agent.mindie.kimi-update schedule with your "
                "platform tools",
    }))
    return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("op", choices=["check", "status", "recover",
                                       "install-schedule", "uninstall-schedule"])
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.config is not None:
        os.environ["MINDIE_KIMI_CONFIG"] = str(args.config.expanduser().absolute())
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
        diagnostic = diagnostic_support.failure(
            "updater",
            "entry",
            "update_failure",
            exception=exc,
            reportable=False,
        )
        print(json.dumps({
            "result": "failed",
            "error_type": type(exc).__name__,
            "diagnostic": diagnostic,
        }))
        raise SystemExit(1)
