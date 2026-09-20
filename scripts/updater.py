#!/usr/bin/env python3
"""MindIE Kimi automatic updater. One bounded check per invocation.

Driven by the OS scheduler (launchd every 5 minutes on macOS; Windows
task registration code is present but not natively verified). No daemon,
no model, no SessionStart work.

check: resolve remote main to one SHA -> stage an immutable generation
with its own pinned venv -> build the native host package -> under the
exclusive operation lock call core stop_if_idle -> install through
Kimi's native plugin API -> atomically swap configs and current.json.
Any failure leaves the current generation fully callable and records an
actionable status. No write retry loop; a failed exact revision is
suppressed until `recover` or a newer revision.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run as bounded_run
from genstate import (
    LockTimeout,
    OperationLock,
    atomic_write,
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
    engine_config_path,
    load_adapter_config,
    load_engine_config,
    state_dir,
)

DEFAULT_REMOTE = "https://github.com/mindie-agent/mindie-agent-kimi.git"
INTERVAL_SECONDS = 300
SHA = r"[0-9a-f]{40}"
COMPLETE = ".mindie-generation-complete"
LAUNCHER = "mindie_launch.py"


class CheckFailed(RuntimeError):
    """A failed check: suppress this exact revision until recover."""


class Deferred(RuntimeError):
    """Not applied now; next normal scheduled check may retry."""


def _git(args, *, timeout, cwd=None):
    return bounded_run(["git", *args], "", timeout=timeout, cwd=cwd)


def resolve_main(remote: str) -> str:
    output = _git(["ls-remote", remote, "refs/heads/main"], timeout=30)
    match = re.search(rf"\b({SHA})\s+refs/heads/main", output)
    if not match:
        raise CheckFailed("remote main did not resolve to a commit SHA")
    return match.group(1)


def _venv_python(venv: Path) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def build_runtime(generation: Path) -> Path:
    """Real pinned venv from the generation's runtime-requirements.txt."""
    requirements = generation / "runtime-requirements.txt"
    text = requirements.read_text()
    if re.search(r"@main\b", text):
        raise CheckFailed("runtime-requirements.txt must pin full commit SHAs, not @main")
    venv = generation / ".venv"
    python = _venv_python(venv)
    bounded_run([sys.executable, "-m", "venv", str(venv)], "", timeout=180)
    bounded_run(
        [str(python), "-m", "pip", "install", "--disable-pip-version-check",
         "--no-input", "-r", str(requirements)],
        "",
        timeout=600,
        max_output=512 * 1024,
    )
    return python


def probe_runtime(python: Path) -> None:
    from setup import PROBE_SCRIPT

    output = bounded_run([str(python), "-c", PROBE_SCRIPT], "", timeout=60)
    if not output.strip().endswith("OK"):
        raise CheckFailed(f"generation runtime probe failed: {output.strip()[:300]}")


def stage_generation(sha: str, remote: str, config, build=build_runtime) -> Path:
    """Clone the exact SHA into a new immutable generation dir with venv."""
    target = generations_dir(config) / sha
    if (target / COMPLETE).is_file():
        return target
    staging = generations_dir(config) / f".staging-{sha}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _git(["init", "-q", str(staging)], timeout=30)
        _git(["remote", "add", "origin", remote], timeout=15, cwd=staging)
        _git(["fetch", "--depth", "1", "origin", sha], timeout=180, cwd=staging)
        _git(["checkout", "-q", "--detach", "FETCH_HEAD"], timeout=60, cwd=staging)
        python = build(staging)
        probe_runtime(python)
        build_host_package(staging, config)
        (staging / COMPLETE).write_text(f"{sha}\n")
        if target.exists():
            shutil.rmtree(staging)
        else:
            os.replace(staging, target)
        return target
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def build_host_package(generation: Path, config) -> Path:
    """Native host package: manifest + Skills + commands, stable launchers."""
    package = generation / "host-package"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir()
    manifest = json.loads((generation / "kimi.plugin.json").read_text())
    launcher = launch_dir(config) / LAUNCHER
    launcher.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(generation / "scripts" / LAUNCHER, launcher)
    base_python = sys.executable
    for surface in ("knowledge", "remote"):
        manifest["mcpServers"][surface] = {
            "command": base_python,
            "args": [str(launcher), "mcp", surface],
        }
    quoted = f"'{base_python}' '{launcher}'" if os.name != "nt" else f'"{base_python}" "{launcher}"'
    for hook in manifest.get("hooks", []):
        op = {"PreToolUse": "pretool", "Stop": "stop"}.get(hook.get("event"))
        if op:
            hook["command"] = f"{quoted} hook {op}"
    for name in ("skills", "commands"):
        source = generation / name
        if source.is_dir():
            shutil.copytree(source, package / name)
    (package / "kimi.plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return package


def stop_if_idle(config) -> dict:
    """Shared core authenticated RPC. Unknown/pending durable receipts and
    idle task grants do not block; only actual active work does."""
    from knowledge_service import existing_service
    from mindie_knowledge.loop.cli import rpc

    try:
        connection = existing_service(engine_config_path(config))
    except Exception:
        return {"idle": True, "service": "not-running"}
    try:
        result = rpc(connection, "stop_if_idle", {}, timeout=5)
    except Exception as exc:
        raise Deferred(f"pinned core does not provide stop_if_idle ({exc})")
    if not isinstance(result, dict) or result.get("idle") is not True:
        raise Deferred("service has actual active work; switch deferred")
    return result


def native_install(adapter: dict, package: Path) -> dict:
    home = adapter.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if not isinstance(home, str) or not home:
        raise CheckFailed("adapter configuration lacks kimi_home for native install")
    output = bounded_run(
        [sys.executable, str(HERE / "install_kimi_plugin.py"),
         "--kimi-home", home, "--plugin-root", str(package)],
        "",
        timeout=120,
    )
    report = json.loads(output)
    install = (report.get("install") or {}).get("body") or {}
    data = install.get("data") or {}
    manifest = json.loads((package / "kimi.plugin.json").read_text())
    if data.get("hasErrors") or data.get("id") != manifest["name"]:
        raise CheckFailed(f"native install rejected: {json.dumps(data)[:300]}")
    after = json.dumps((report.get("after") or {}).get("body") or {})
    if manifest["name"] not in after or manifest.get("version", "") not in after:
        raise CheckFailed("native readback did not confirm name/version")
    return report


def swap_configs(config, generation: Path, python: Path, sha: str) -> None:
    """Atomic pointer updates under the exclusive lock. Receipts enable
    rollback; current.json flips last."""
    from paths import config_path

    adapter_file = config_path()
    adapter = load_adapter_config()
    engine_file = engine_config_path(adapter)
    engine = load_engine_config(adapter)
    receipts_dir(config).mkdir(parents=True, exist_ok=True)
    atomic_write(
        receipts_dir(config) / f"{sha}.json",
        {
            "sha": sha,
            "at": time.time(),
            "previous": read_current(config),
            "adapter_config": adapter,
            "engine_config": engine,
            "generation": str(generation),
        },
    )
    new_engine = dict(
        engine,
        transcript_adapter=str(generation / "scripts" / "transcript.py"),
        agent_command=[str(python), str(generation / "scripts" / "organizer.py")],
    )
    atomic_write(engine_file, new_engine)
    atomic_write(adapter_file, dict(adapter, python=str(python)))
    write_current(
        {"generation": str(generation), "python": str(python), "sha": sha}, config
    )


def rollback(receipt_sha: str, config) -> None:
    receipt = read_json(receipts_dir(config) / f"{receipt_sha}.json")
    if not receipt:
        return
    from paths import config_path

    try:
        if isinstance(receipt.get("engine_config"), dict):
            atomic_write(engine_config_path(config), receipt["engine_config"])
        if isinstance(receipt.get("adapter_config"), dict):
            atomic_write(config_path(), receipt["adapter_config"])
        previous = receipt.get("previous")
        if isinstance(previous, dict) and previous.get("generation"):
            write_current(previous, config)
    except OSError:
        pass


def _record(config, **fields) -> dict:
    status = dict(read_status(config), at=time.time(), **fields)
    write_status(status, config)
    return status


def _fail(config, sha, exc) -> int:
    if sha:
        atomic_write(
            failed_path(config),
            {"sha": sha, "error": str(exc)[:500], "at": time.time()},
        )
    _record(config, candidate_sha=sha, result="failed", error=str(exc)[:500])
    print(json.dumps({"result": "failed", "error": str(exc)[:500]}))
    return 1


def check(config=None, *, force=False, build=build_runtime,
          idle=stop_if_idle, install=native_install, lock_timeout=30.0) -> int:
    adapter = config if isinstance(config, dict) else load_adapter_config()
    update_dir(adapter).mkdir(parents=True, exist_ok=True)
    current = read_current(adapter)
    remote = (
        os.environ.get("MINDIE_KIMI_UPDATE_REMOTE")
        or adapter.get("update_remote")
        or DEFAULT_REMOTE
    )
    try:
        sha = resolve_main(remote)
    except Exception as exc:
        _record(adapter, current_sha=current.get("sha"), result="check-failed",
                error=str(exc)[:500])
        print(json.dumps({"result": "check-failed", "error": str(exc)[:300]}))
        return 1
    failed = read_json(failed_path(adapter))
    if (
        not force
        and failed
        and failed.get("sha") == sha
        and current.get("sha") != sha
    ):
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="suppressed-known-failed", error=failed.get("error"))
        print(json.dumps({"result": "suppressed-known-failed", "sha": sha}))
        return 0
    if sha == current.get("sha"):
        status = _record(adapter, current_sha=sha, result="current")
        _feed_sync(adapter, status)
        print(json.dumps({"result": "current", "sha": sha}))
        return 0
    try:
        generation = stage_generation(sha, remote, adapter, build=build)
    except Deferred as exc:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="deferred", error=str(exc)[:500])
        return 0
    except Exception as exc:
        return _fail(adapter, sha, exc)
    python = _venv_python(generation / ".venv")
    try:
        with OperationLock(adapter).exclusive(timeout=lock_timeout):
            idle_result = idle(adapter)
            if isinstance(idle_result, dict) and idle_result.get("idle") is not True:
                raise Deferred("service has actual active work; switch deferred")
            install(adapter, generation / "host-package")
            try:
                swap_configs(adapter, generation, python, sha)
            except Exception:
                rollback(sha, adapter)
                raise
    except LockTimeout:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="deferred-busy", error="operation lock stayed shared")
        print(json.dumps({"result": "deferred-busy", "sha": sha}))
        return 0
    except Deferred as exc:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result="deferred", error=str(exc)[:500])
        print(json.dumps({"result": "deferred", "sha": sha, "reason": str(exc)[:300]}))
        return 0
    except CheckFailed as exc:
        rollback(sha, adapter)
        return _fail(adapter, sha, exc)
    except Exception as exc:
        rollback(sha, adapter)
        return _fail(adapter, sha, exc)
    status = _record(
        adapter,
        current_sha=sha,
        result="switched",
        needs_host_reload=True,
        note=(
            "MCP/hook dispatch now uses the new generation for new calls; "
            "already-loaded sessions keep their old generation. Native "
            "Skills/commands/hook definitions load on Kimi host reload."
        ),
    )
    _feed_sync(adapter, status)
    print(json.dumps({"result": "switched", "sha": sha, "needs_host_reload": True}))
    return 0


def _feed_sync(adapter, status) -> None:
    """Best-effort knowledge feed sync through shared core. Never starts an
    organizer or a model; failure is recorded, not retried."""
    engine = adapter.get("engine_config")
    if not isinstance(engine, str):
        return
    current = read_current(adapter)
    python = current.get("python") or sys.executable
    try:
        bounded_run(
            [python, "-m", "mindie_knowledge.loop.cli", "sync", engine],
            "",
            timeout=120,
        )
        status["feed_sync"] = "ok"
    except Exception as exc:
        status["feed_sync"] = f"skipped: {str(exc)[:200]}"
    write_status(status, adapter)


def status() -> int:
    try:
        adapter = load_adapter_config()
    except FileNotFoundError:
        print(json.dumps({"result": "unconfigured",
                          "hint": "run scripts/setup.py first"}))
        return 0
    current = read_current(adapter)
    base = generations_dir(adapter)
    generations = sorted(
        p.name for p in base.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ) if base.is_dir() else []
    print(json.dumps({
        "current": current,
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


def _schedule_command(config_path: Path) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "check",
            "--config", str(config_path)]


def install_schedule() -> int:
    from paths import config_path

    adapter = load_adapter_config()
    command = _schedule_command(config_path())
    log = update_dir(adapter) / "scheduler.log"
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"    <string>{item}</string>\n" for item in command)
        plist.write_text(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" "
            "\"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n"
            "<plist version=\"1.0\"><dict>\n"
            "  <key>Label</key><string>agent.mindie.kimi-update</string>\n"
            "  <key>ProgramArguments</key><array>\n" + args + "  </array>\n"
            f"  <key>StartInterval</key><integer>{INTERVAL_SECONDS}</integer>\n"
            f"  <key>StandardOutPath</key><string>{log}</string>\n"
            f"  <key>StandardErrorPath</key><string>{log}</string>\n"
            "</dict></plist>\n"
        )
        uid = os.getuid()
        try:
            bounded_run(["launchctl", "bootout", f"gui/{uid}", str(plist)], "", timeout=15)
        except RuntimeError:
            pass
        bounded_run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], "", timeout=15)
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


def uninstall_schedule() -> int:
    if sys.platform == "darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        if plist.exists():
            try:
                bounded_run(["launchctl", "bootout", f"gui/{os.getuid()}",
                             str(plist)], "", timeout=15)
            except RuntimeError:
                pass
            plist.unlink()
    elif os.name == "nt":
        try:
            bounded_run(["schtasks", "/Delete", "/F", "/TN", "MindIEKimiUpdate"],
                        "", timeout=30)
        except RuntimeError:
            pass
    print(json.dumps({"result": "unscheduled"}))
    return 0


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
        return install_schedule()
    return uninstall_schedule()


if __name__ == "__main__":
    raise SystemExit(main())
