#!/usr/bin/env python3
"""MindIE Kimi automatic updater and feed-sync runner.

[Role]
Executes a single bounded update check (`check`), status inspection (`status`),
explicit recovery (`recover`), or scheduler registration (`install-schedule` /
`uninstall-schedule`). Selects a compatible formal Release, stages its exact generation
with its own pinned venv and versioned launcher, switches `current.json` under
the exclusive `operation.lock` after `stop_if_idle` and native plugin install
readback, and runs independent knowledge feed sync.

[Invariants]
- Live entrypoints are never mutated during staging; each generation gets a
  versioned launcher under `update/launch/<sha>/mindie_launch.py`.
- Switch requires an idle knowledge service (`stop_if_idle`), verified native
  plugin readback, and atomic `current.json` replacement; failures roll back both
  `current.json` and the native plugin package under the same exclusive lock.
- Knowledge feed sync (`_feed_sync`) and diagnostic retention maintenance run
  inside reserved tail budgets even when candidate preparation fails.

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
from datetime import datetime, timedelta
import json
import os
import random
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
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

_FEED_OK = frozenset({"synced", "unchanged"})
_FEED_PENDING = frozenset({"busy", "deferred"})
_FEED_ERROR = frozenset({"unavailable", "invalid", "exhausted"})
_FEED_KNOWN = _FEED_OK | _FEED_PENDING | _FEED_ERROR


def _feed_error_summary(rows):
    parts = [
        f"{row['repository']}:{row['status']}:{str(row.get('detail') or row.get('cause') or '')[:80]}"
        for row in rows if row["status"] not in _FEED_OK
    ]
    return "; ".join(parts)[:240]


def fold_feed_results(output):
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
        status_val = item.get("status")
        repo = item.get("repository")
        if not isinstance(status_val, str) or not status_val:
            raise ValueError("knowledge sync row missing status")
        if not isinstance(repo, str) or not repo:
            raise ValueError("knowledge sync row missing repository")
        if status_val not in _FEED_KNOWN:
            raise ValueError("knowledge sync unknown status: " + status_val)
        rows.append(item)
    good = sum(1 for row in rows if row["status"] in _FEED_OK)
    pending = sum(1 for row in rows if row["status"] in _FEED_PENDING)
    failed = sum(1 for row in rows if row["status"] in _FEED_ERROR)
    if not rows or good == len(rows):
        return "ok", rows, None
    summary = _feed_error_summary(rows)
    if good and (pending or failed):
        return "degraded", rows, summary
    if failed:
        return "sync_failed", rows, summary
    if pending:
        return "deferred", rows, summary
    return "sync_failed", rows, summary


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
    except (OSError, ValueError, FileNotFoundError):
        pass
    try:
        from paths import resolve_git_bin

        resolved = resolve_git_bin()
        if isinstance(resolved, str) and resolved:
            return resolved
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


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def build_runtime(generation: Path, deadline: float) -> Path:
    if sys.version_info < MIN_PYTHON:
        raise CheckFailed(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required")
    requirements = generation / "runtime-requirements.txt"
    if re.search(r"@main\b", requirements.read_text(encoding="utf-8")):
        raise CheckFailed("runtime-requirements.txt must pin full commit SHAs, not @main")
    venv = generation / ".venv"
    python = _venv_python(venv)
    uv = shutil.which("uv")
    from setup import installer_environment

    if uv is None:
        raise CheckFailed("uv is required to prepare the MindIE runtime")
    env = installer_environment()
    bounded_run([uv, "venv", "--python", sys.executable, str(venv)], "",
                timeout=min(180, _remaining(deadline, RESERVE)), env=env)
    bounded_run(
        [uv, "pip", "install", "--python", str(python), "-r", str(requirements)],
        "", timeout=min(600, _remaining(deadline, RESERVE)),
        max_output=512 * 1024, env=env, transport=True,
    )
    return python


def probe_runtime(python: Path, deadline: float, generation: Path) -> None:
    from setup import probe_script

    parser = str(generation / "scripts" / "transcript.py")
    output = bounded_run([str(python), "-c", probe_script(parser)], "", timeout=min(60, _remaining(deadline, RESERVE)))
    if not output.strip().endswith("OK"):
        raise CheckFailed(f"generation runtime probe failed: {output.strip()[:300]}")


def _profile_authority_sources(adapter: dict):
    from paths import engine_config_path as _engine_path, profile_config_path

    profile = adapter.get("profile_config")
    if not (isinstance(profile, str) and os.path.isabs(profile)):
        profile = str(profile_config_path())
    padapter = None
    try:
        candidate = json.loads(Path(profile).read_text(encoding="utf-8"))
        if isinstance(candidate, dict):
            padapter = candidate
    except (OSError, ValueError):
        pass
    pengine = None
    if padapter is not None:
        try:
            value = json.loads(Path(_engine_path(padapter)).read_text(encoding="utf-8"))
            if isinstance(value, dict):
                pengine = value
        except (OSError, ValueError, FileNotFoundError):
            pass
    return padapter, pengine


def write_generation_configs(generation: Path, python: Path, adapter: dict) -> Path:
    current = read_current(adapter)
    active = read_json(Path(current['adapter_config']))
    if active is not None:
        adapter = active
    engine = load_engine_config(adapter)
    engine.pop("agent_command", None)
    padapter, pengine = _profile_authority_sources(adapter)
    if pengine is not None:
        for key in ("community_config", "admission_path"):
            value = pengine.get(key)
            if isinstance(value, str) and os.path.isabs(value):
                engine[key] = value
    config_dir = generation / "config"
    gen_engine = config_dir / "kimi.engine.json"
    gen_adapter = config_dir / "kimi.adapter.json"
    atomic_write(
        gen_engine,
        dict(
            engine,
            transcript_adapter=str(generation / "scripts" / "transcript.py"),
            **__import__("capture_config").prepare(python, generation / "scripts"),
        ),
    )
    profile = (padapter or adapter).get("profile_config")
    if not (isinstance(profile, str) and os.path.isabs(profile)):
        from paths import profile_config_path

        profile = str(profile_config_path())
    gen_adapter_value = dict(
        adapter,
        python=str(python),
        engine_config=str(gen_engine),
        profile_config=profile,
    )
    if padapter is not None and isinstance(padapter.get("community_config"), str):
        gen_adapter_value["community_config"] = padapter["community_config"]
    kimi_bin = adapter.get("kimi_bin")
    if not (isinstance(kimi_bin, str) and kimi_bin):
        from paths import resolve_kimi_bin

        try:
            kimi_bin = resolve_kimi_bin(padapter or adapter)
        except (OSError, ValueError, FileNotFoundError):
            kimi_bin = None
    if isinstance(kimi_bin, str) and kimi_bin:
        gen_adapter_value["kimi_bin"] = kimi_bin
    git_bin = adapter.get("git_bin")
    if not (isinstance(git_bin, str) and git_bin):
        from paths import resolve_git_bin

        try:
            git_bin = resolve_git_bin()
        except (OSError, ValueError):
            git_bin = None
    if isinstance(git_bin, str) and git_bin:
        gen_adapter_value["git_bin"] = git_bin
    gen_adapter_value['domain_source_configs'] = dict(adapter.get('domain_configs') or {})
    from capture_config import prepare_domains
    gen_adapter_value['domain_configs'] = prepare_domains(
        python, gen_adapter_value, read_json(gen_engine), config_directory=str(config_dir / 'domains'),
        authority_directory=str(Path(profile).parent / 'knowledge-domains'),
        consent_config=str(Path(profile).with_name('mindie-consent.json')),
        public_feeds=bool(engine.get('feeds')))
    atomic_write(gen_adapter, gen_adapter_value)
    return gen_adapter


def host_valid_mcp_command(command: str) -> bool:
    if not isinstance(command, str) or not command:
        return False
    if command.startswith("./"):
        return True
    return not (os.path.isabs(command) or "/" in command or "\\" in command)


def _write_front_wrapper(package: Path, python: Path) -> str:
    name = "mindie-front"
    (package / name).write_text(f'#!/bin/sh\nexec {shlex.quote(str(python))} "$@"\n')
    (package / name).chmod(0o755)
    return f"./{name}"


def _quote_front_command(python, launcher, config_file) -> str:
    parts = [str(python), str(launcher), "--config", str(config_file)]
    return " ".join(f'"{p}"' for p in parts) if os.name == "nt" else " ".join(shlex.quote(p) for p in parts)


def schedule_command(launcher: Path, config_file: Path) -> list[str]:
    return [sys.executable, str(launcher), "--config", str(config_file), "updater", "check"]


def build_host_package(
    generation: Path, adapter: dict, sha: str,
    package_dir: Path | None = None, config_file=None,
) -> Path:
    if config_file is None:
        raise ValueError("build_host_package requires an explicit config_file")
    config_file = Path(config_file)
    launcher_dir = launch_dir(adapter) / sha
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher = launcher_dir / LAUNCHER
    if not launcher.is_file():
        for name in (LAUNCHER, "bounded.py", "diagnostic_support.py", "paths.py"):
            shutil.copy2(generation / "scripts" / name, launcher_dir / name)
    package = package_dir if package_dir is not None else generation / "host-package"
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)
    manifest = json.loads((generation / "kimi.plugin.json").read_text(encoding="utf-8"))
    base = str(manifest.get("version") or "0.0.1").split("+")[0]
    manifest["version"] = f"{base}+mindie.{sha[:12]}"
    base_python = sys.executable
    if os.name == "nt":
        front = Path(base_python).name
        front_env = {"PATH": str(Path(base_python).parent) + os.pathsep + os.environ.get("PATH", "")}
    else:
        front = _write_front_wrapper(package, Path(base_python))
        front_env = None
    bound = ["--config", str(config_file)]
    for surface in ("knowledge", "remote"):
        manifest["mcpServers"][surface] = {
            "command": front,
            "args": [str(launcher), *bound, "mcp", surface],
        }
        if front_env is not None:
            manifest["mcpServers"][surface]["env"] = dict(front_env)
    quoted = _quote_front_command(base_python, launcher, config_file)
    for hook in manifest.get("hooks", []):
        if op := {"PreToolUse": "pretool", "Stop": "stop"}.get(hook.get("event")):
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


def _remove_owned_tree(path):
    def retry_readonly(function, filename, exc_info):
        error = exc_info[1]
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(filename, stat.S_IWRITE | stat.S_IREAD)
        function(filename)

    shutil.rmtree(path, onerror=retry_readonly)


def stage_generation(
    sha: str, remote: str, adapter: dict, deadline: float,
    build=build_runtime, release=None,
) -> tuple[Path, Path, Path]:
    target = generations_dir(adapter) / sha
    if (target / COMPLETE).is_file():
        if release is not None:
            _verify_generation_release(target, sha, release)
        python = _venv_python(target / ".venv")
        gen_adapter = target / "config" / "kimi.adapter.json"
        if python.exists() and gen_adapter.is_file():
            current = read_current(adapter)
            active, candidate = read_json(Path(current['adapter_config'])), read_json(gen_adapter)
            if (current.get('sha') != sha and active is not None and candidate is not None
                    and candidate.get('domain_source_configs') != (active.get('domain_configs') or {})):
                # A refreshed but unpublished generation is complete only
                # after both its binding and host package were rebuilt.
                (target / COMPLETE).unlink()
                gen_adapter = write_generation_configs(target, python, active)
                build_host_package(target, active, sha, config_file=gen_adapter)
                (target / COMPLETE).write_text(f"{sha}\n")
            return target, python, gen_adapter
        _remove_owned_tree(target)
    elif target.exists():
        _remove_owned_tree(target)
    staging = generations_dir(adapter) / f".staging-{sha}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _git(["init", "-q", str(staging)], timeout=30, deadline=deadline)
        _git(["remote", "add", "origin", remote], timeout=15, deadline=deadline, cwd=staging)
        _git(["fetch", "--depth", "1", "origin", sha], timeout=180, deadline=deadline, cwd=staging, transport=True)
        _git(["checkout", "-q", "--detach", "FETCH_HEAD"], timeout=60, deadline=deadline, cwd=staging)
        os.replace(staging, target)
    except Exception:
        _remove_owned_tree(staging)
        raise
    try:
        if release is not None:
            _verify_generation_release(target, sha, release)
        python = build(target, deadline)
        probe_runtime(python, deadline, target)
        gen_adapter = write_generation_configs(target, python, adapter)
        build_host_package(target, adapter, sha, config_file=gen_adapter)
        (target / COMPLETE).write_text(f"{sha}\n")
        return target, python, gen_adapter
    except Exception:
        _remove_owned_tree(target)
        raise


def _verify_generation_release(generation: Path, sha: str, release: dict) -> None:
    from release_metadata import metadata

    if metadata(generation, sha) != release:
        raise CheckFailed("fetched source does not match the formal Release metadata")


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
    except Exception as exc:
        try:
            after = _readback(adapter, deadline, reserve)
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
        except Exception:
            final = None
            pointer_error = None
            try:
                write_current(current, adapter)
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


def _check_once(adapter: dict, deadline: float, *, force, build, idle, install, lock_timeout) -> int:
    current = read_current(adapter)
    remote = os.environ.get("MINDIE_KIMI_UPDATE_REMOTE") or adapter.get("update_remote") or DEFAULT_REMOTE
    if _resolve_is_waiting(adapter, force):
        status = read_status(adapter)
        print(
            json.dumps({
                "result": "retry-waiting" if status.get("resolve_failure_class") in _RETRYABLE else status.get("result", "check-failed"),
                "error": status.get("error"),
                "failure_class": status.get("resolve_failure_class"),
                "next_retry_at": status.get("next_retry_at"),
            })
        )
        return 0
    try:
        selected = resolve_release(remote, adapter, current, deadline)
    except Exception as exc:
        recorded = _note_resolve_failure(adapter, exc, current.get("sha"))
        print(
            json.dumps({
                "result": recorded.get("result", "check-failed"),
                "error": recorded.get("error"),
                "failure_class": recorded.get("resolve_failure_class"),
                "next_retry_at": recorded.get("next_retry_at"),
                "diagnostic": recorded.get("diagnostic"),
            })
        )
        return 1
    _clear_resolve_wait(adapter)
    candidate = selected["candidate"]
    if candidate is None:
        result = "current" if selected["status"] == "up_to_date" else selected["status"]
        status = _record(
            adapter, current_sha=current.get("sha"), result=result, error=None,
            release_selection=selected,
        )
        print(json.dumps(status))
        return int(result == "no_compatible_release" or status["result"] == "degraded")
    release = candidate["metadata"]
    sha = release["source"]["sha"]
    failed = read_json(failed_path(adapter))
    if not force and isinstance(failed, dict) and failed.get("sha") == sha and current.get("sha") != sha:
        if _failure_gate(failed) == "wait":
            result_name = "action-required" if failed.get("failure_class") in _ACTIONABLE else "retry-waiting"
            _record(
                adapter, current_sha=current.get("sha"), candidate_sha=sha,
                result=result_name, error=failed.get("error"),
                failure_class=failed.get("failure_class"),
                next_retry_at=failed.get("next_retry_at"),
            )
            print(
                json.dumps({
                    "result": result_name, "sha": sha,
                    "failure_class": failed.get("failure_class"),
                    "next_retry_at": failed.get("next_retry_at"),
                    "error": failed.get("error"),
                })
            )
            return 0
    if sha == current.get("sha"):
        _clear_active_failure(adapter, sha)
        status = _record(adapter, current_sha=sha, result="current", error=None)
        print(json.dumps(status))
        return int(status["result"] == "degraded")
    try:
        generation, python, gen_adapter = stage_generation(
            sha, remote, adapter, deadline, build=build, release=release,
        )
    except Exception as exc:
        record = _remember_failure(adapter, sha, exc)
        diagnostic = _update_diagnostic("stage_generation", exc)
        _record(
            adapter, current_sha=current.get("sha"), candidate_sha=sha,
            result="failed", error=record.get("error"),
            failure_class=record.get("failure_class"),
            next_retry_at=record.get("next_retry_at"), diagnostic=diagnostic,
        )
        print(
            json.dumps({
                "result": "failed", "error": record.get("error"),
                "failure_class": record.get("failure_class"),
                "next_retry_at": record.get("next_retry_at"),
                "diagnostic": diagnostic,
            })
        )
        return 1
    try:
        switch(adapter, current, generation, python, gen_adapter, sha, deadline, idle, install, lock_timeout)
    except LockTimeout:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha, result="deferred-busy", error="operation lock stayed shared")
        print(json.dumps({"result": "deferred-busy", "sha": sha}))
        return 0
    except Deferred as exc:
        _record(adapter, current_sha=current.get("sha"), candidate_sha=sha, result="deferred", error=str(exc)[:500])
        print(json.dumps({"result": "deferred", "sha": sha, "reason": str(exc)[:300]}))
        return 0
    except Exception as exc:
        record = _remember_failure(adapter, sha, exc)
        diagnostic = _update_diagnostic("switch", exc)
        _record(
            adapter, current_sha=current.get("sha"), candidate_sha=sha,
            result="failed", error=record.get("error"),
            failure_class=record.get("failure_class"),
            next_retry_at=record.get("next_retry_at"), diagnostic=diagnostic,
        )
        print(
            json.dumps({
                "result": "failed", "error": record.get("error"),
                "failure_class": record.get("failure_class"),
                "next_retry_at": record.get("next_retry_at"),
                "diagnostic": diagnostic,
            })
        )
        return 1
    _clear_active_failure(adapter, sha)
    status = _record(
        adapter, current_sha=sha, result="switched", needs_host_reload=True, error=None,
        note=(
            "MCP/hook dispatch uses the new generation per call; "
            "existing task authorization is preserved. Native "
            "Skill/command/MCP/hook definitions refresh with the Kimi host."
        ),
    )
    print(json.dumps(status))
    return int(status["result"] == "degraded")


def check(
    config=None, *, force=False, build=build_runtime,
    idle=stop_if_idle, install=native_install, lock_timeout=30.0,
) -> int:
    adapter = config if isinstance(config, dict) else load_adapter_config()
    update_dir(adapter).mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + CHECK_BUDGET
    try:
        with check_lock(adapter):
            code = _check_once(
                adapter, deadline, force=force, build=build,
                idle=idle, install=install, lock_timeout=lock_timeout,
            )
            _feed_sync(adapter, deadline)
            try:
                _logging_maintenance(adapter, deadline)
            except Exception as exc:
                _update_diagnostic("logging_maintenance", exc)
            return code
    except LockTimeout:
        print(json.dumps({"result": "check-already-running"}))
        return 0


def _logging_maintenance(adapter, deadline):
    status = read_status(adapter)
    remaining = deadline - time.monotonic()
    if remaining <= 0.1:
        status["logging_maintenance"] = {"status": "skipped_budget"}
        write_status(status, adapter)
        return
    try:
        with OperationLock(adapter).shared(timeout=min(0.5, remaining)):
            current = read_current(adapter)
        available = min(75, deadline - time.monotonic())
        if available <= 0:
            status["logging_maintenance"] = {"status": "skipped_budget"}
            write_status(status, adapter)
            return
        output = bounded_run(
            [
                current["python"], "-m", "mindie_diagnostics.cli",
                "reporting", "maintain", "--update-running", "--config",
                str(diagnostic_support.reporting_config_path()),
                "--budget-seconds", str(max(0, available - 2)),
            ],
            "", timeout=available, max_output=65536, check=False,
        )
        payload = json.loads((output or "").strip() or "null")
        if not isinstance(payload, dict):
            raise ValueError("maintenance result is not an object")
        status["logging_maintenance"] = payload
    except LockTimeout:
        status["logging_maintenance"] = {"status": "deferred"}
    except Exception as exc:
        status["logging_maintenance"] = {"status": "unavailable", "type": type(exc).__name__}
    write_status(status, adapter)


def _feed_sync(adapter: dict, deadline: float) -> bool:
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
            git_bin = adapter.get("git_bin")
            env = dict(os.environ, MINDIE_GIT_BIN=git_bin) if isinstance(git_bin, str) and git_bin else None
            output = bounded_run(
                [current["python"], "-m", "mindie_knowledge.loop.cli", "sync", "--config", engine],
                "", timeout=min(FEED_BUDGET, deadline - time.monotonic()), env=env,
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


def _wrapper_body(command) -> str:
    # Keep a hidden console for the launcher and its ordinary console children.
    # CREATE_NO_WINDOW alone would let those children allocate a visible console.
    return (
        "import subprocess\n"
        "startup = subprocess.STARTUPINFO()\n"
        "startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW\n"
        "startup.wShowWindow = subprocess.SW_HIDE\n"
        f"raise SystemExit(subprocess.call({list(command)!r},\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE))\n"
    )


def _schedule_wrapper(adapter: dict, command) -> Path:
    wrapper = update_dir(adapter) / "MindIEKimiUpdate.pyw"
    update_dir(adapter).mkdir(parents=True, exist_ok=True)
    body = _wrapper_body(command)
    tmp = wrapper.with_name(wrapper.name + f".tmp-{os.getpid()}")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, wrapper)
    return wrapper


def _verify_scheduled_task(wrapper: Path, pythonw: Path) -> None:
    # schtasks writes its XML in the console codepage. Export Unicode directly
    # so bounded_run's UTF-8 transport also preserves non-ASCII paths.
    output = bounded_run([
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
        "Export-ScheduledTask -TaskName MindIEKimiUpdate -ErrorAction Stop",
    ], "", timeout=30)
    try:
        actions = ET.fromstring(output).findall("./{*}Actions/{*}Exec")
    except ET.ParseError:
        actions = []
    if (len(actions) != 1
            or actions[0].findtext("{*}Command") != str(pythonw)
            or actions[0].findtext("{*}Arguments") != subprocess.list2cmdline([str(wrapper)])):
        raise SystemExit(
            "scheduled task readback does not match the windowless wrapper action; "
            "the registration is not trusted"
        )


def _register_windows_schedule(adapter: dict, command) -> dict:
    pythonw = Path(command[0]).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise SystemExit(f"windowless Python is unavailable at {pythonw}; task was not registered")
    wrapper = _schedule_wrapper(adapter, command)
    # XML keeps the interpreter and wrapper paths separate, without /TR's
    # command-line length limit or cmd.exe's codepage and metacharacter parsing.
    task = ET.Element("Task", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task", version="1.2")
    trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "TimeTrigger")
    start = datetime.now() + timedelta(seconds=INTERVAL_SECONDS)
    ET.SubElement(trigger, "StartBoundary").text = start.isoformat(timespec="seconds")
    ET.SubElement(ET.SubElement(trigger, "Repetition"), "Interval").text = "PT5M"
    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="Author")
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    settings = ET.SubElement(task, "Settings")
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    action = ET.SubElement(ET.SubElement(task, "Actions", Context="Author"), "Exec")
    ET.SubElement(action, "Command").text = str(pythonw)
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline([str(wrapper)])
    definition = wrapper.with_suffix(".xml")
    ET.ElementTree(task).write(definition, encoding="utf-16", xml_declaration=True)
    bounded_run(
        ["schtasks", "/Create", "/F", "/TN", "MindIEKimiUpdate", "/XML", str(definition)],
        "", timeout=30,
    )
    _verify_scheduled_task(wrapper, pythonw)
    return {"result": "scheduled", "task": "MindIEKimiUpdate", "action": str(wrapper), "verified": True}


def install_schedule(config_file=None) -> int:
    if config_file is not None:
        config_file = Path(config_file).expanduser().absolute()
        adapter = json.loads(config_file.read_text(encoding="utf-8"))
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
    from mindie_diagnostics.service_environment import prepare
    prepare(update_dir(adapter))
    if sys.platform == "darwin":
        import plistlib

        plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "agent.mindie.kimi-update",
                    "ProgramArguments": command,
                    "EnvironmentVariables": {
                        "PATH": os.environ.get("PATH")
                        or "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                    },
                    "StartInterval": INTERVAL_SECONDS,
                    "StandardOutPath": os.devnull,
                    "StandardErrorPath": os.devnull,
                },
                stream,
            )
        uid = os.getuid()
        try:
            bounded_run(["launchctl", "bootout", f"gui/{uid}", str(plist)], "", timeout=15)
        except RuntimeError:
            pass
        bounded_run(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], "", timeout=15)
        print(json.dumps({"result": "scheduled", "agent": str(plist), "interval_seconds": INTERVAL_SECONDS}))
        return 0
    if os.name == "nt":
        print(json.dumps(_register_windows_schedule(adapter, command)))
        return 0
    print(
        json.dumps({
            "result": "unsupported-platform",
            "hint": f"run every {INTERVAL_SECONDS}s via your scheduler: " + " ".join(command),
        })
    )
    return 1


def _native_run(argv, timeout):
    completed = subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        errors="replace", timeout=timeout,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _launchd_state(label, uid, timeout=10):
    try:
        code, _, stderr = _native_run(["launchctl", "print", f"gui/{uid}/{label}"], timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"[:240]
    if code == 0:
        return "loaded", ""
    if code == 113:
        return "absent", ""
    return "unknown", (stderr or "").strip()[:240] or f"launchctl print exited {code}"


def _task_state(task, timeout=30):
    escaped = task.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$m = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { "
        f"$_.TaskName -eq '{escaped}' -and $_.TaskPath -eq '\\' }}); "
        "Write-Output $m.Count"
    )
    try:
        code, stdout, stderr = _native_run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout
        )
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
    uid = os.getuid()
    target = f"gui/{uid}/{label}"
    state, detail = _launchd_state(label, uid)
    if state == "unknown":
        return _schedule_failed("query", "manager-or-permission", plist=plist, detail=detail)
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
                plist=plist, detail=detail or bootout_detail,
            )
    plist.unlink(missing_ok=True)
    print(json.dumps({"result": "unscheduled"}))
    return 0


def _uninstall_task(task):
    state, detail = _task_state(task)
    if state == "unknown":
        return _schedule_failed("query", "manager-or-permission", task=task, detail=detail)
    if state == "present":
        escaped = task.replace("'", "''")
        script = f"Unregister-ScheduledTask -TaskName '{escaped}' -TaskPath '\\' -Confirm:$false -ErrorAction Stop"
        try:
            code, _, stderr = _native_run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 30
            )
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
                task=task, detail=detail,
            )
    print(json.dumps({"result": "unscheduled"}))
    return 0


def uninstall_schedule(*, label=None, plist_path=None, schedule_root=None, task=None) -> int:
    if sys.platform == "darwin":
        label = label or "agent.mindie.kimi-update"
        if plist_path is not None:
            plist = Path(plist_path).expanduser().absolute()
        else:
            root = Path(schedule_root).expanduser().absolute() if schedule_root else Path.home() / "Library" / "LaunchAgents"
            plist = root / (label + ".plist")
        return _uninstall_launchd(label, plist)
    if os.name == "nt":
        return _uninstall_task(task or "MindIEKimiUpdate")
    print(
        json.dumps({
            "result": "unsupported-platform",
            "hint": "remove the agent.mindie.kimi-update schedule with your platform tools",
        })
    )
    return 1


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
