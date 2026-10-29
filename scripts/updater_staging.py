#!/usr/bin/env python3
"""Candidate-generation runtime and host-package staging for the updater.

[Role]
Pins and probes a candidate generation's runtime, materializes generation
adapter/engine configs, builds the native host package with the versioned
launcher, repairs incomplete native packages, and stages owned generation trees. `updater.py` re-exports
these helpers and binds itself so monkeypatches keep resolving through it.

[Invariants]
- A generation is only marked complete after runtime probe, config write, and
  host-package build all succeed; any failure removes the owned tree.
- Published launchers are versioned under `update/launch/<sha>` and never
  mutate a live generation.
- Host MCP commands remain native-valid (literal `python3`, or `./mindie-front`).

[Timeouts & Locks]
- All subprocess work is bounded by the caller deadline with `RESERVE` held
  back; package repair holds the existing exclusive operation lock after idle.

[Side-Effect Policy]
- Writes only under `update/generations/<sha>`, `update/launch/<sha>`, and the
  caller-provided host-package directory; removal only targets owned trees.
- Repair reuses native install/readback and rollback before recording completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import sys
import time
from pathlib import Path

try:
    from bounded import run as bounded_run
    from genstate import (
        atomic_write,
        current_path,
        generations_dir,
        launch_dir,
        read_current,
        read_json,
        update_dir,
        write_status,
    )
    from paths import config_path, load_engine_config
except ImportError:
    pass

MIN_PYTHON = (3, 12) if os.name == "nt" else (3, 11)
RESERVE = 113.0
SHA = r"[0-9a-f]{40}"
COMPLETE = ".mindie-generation-complete"
LAUNCHER = "mindie_launch.py"
# Kimi's host schema names the per-server tool deadline `toolTimeoutMs`
# (observed in the host's own mcp.json and plugin API). Legacy 0.1.2/0.1.3
# manifests predate the field; the published 0.1.3+ value is 660s.
REMOTE_TOOL_TIMEOUT_MS = 660000
REMOTE_PROFILE = "kimi"

_HOST = None


def bind(host):
    """Bind to the re-exporting `updater` module for patchable lookups."""
    global _HOST
    _HOST = host


def _h(name):
    if _HOST is not None:
        try:
            return getattr(_HOST, name)
        except AttributeError:
            pass
    return globals()[name]


class CheckFailed(RuntimeError):
    """Fallback failure type; `updater.CheckFailed` wins once bound."""


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def build_runtime(generation: Path, deadline: float) -> Path:
    if sys.version_info < _h("MIN_PYTHON"):
        raise _h("CheckFailed")(f"Python {_h('MIN_PYTHON')[0]}.{_h('MIN_PYTHON')[1]}+ is required")
    requirements = generation / "runtime-requirements.txt"
    if re.search(r"@main\b", requirements.read_text(encoding="utf-8")):
        raise _h("CheckFailed")("runtime-requirements.txt must pin full commit SHAs, not @main")
    venv = generation / ".venv"
    python = _h("_venv_python")(venv)
    uv = shutil.which("uv")
    from setup import installer_environment

    if uv is None:
        raise _h("CheckFailed")("uv is required to prepare the MindIE runtime")
    env = installer_environment()
    _h("bounded_run")([uv, "venv", "--python", sys.executable, str(venv)], "",
                      timeout=min(180, _h("_remaining")(deadline, _h("RESERVE"))), env=env)
    _h("bounded_run")(
        [uv, "pip", "install", "--python", str(python), "-r", str(requirements),
         "--overrides", str(requirements)],
        "", timeout=min(600, _h("_remaining")(deadline, _h("RESERVE"))),
        max_output=512 * 1024, env=env, transport=True,
    )
    return python


def probe_runtime(python: Path, deadline: float, generation: Path) -> None:
    from setup import probe_script

    parser = str(generation / "scripts" / "transcript.py")
    output = _h("bounded_run")(
        [str(python), "-c", probe_script(parser)], "",
        timeout=min(60, _h("_remaining")(deadline, _h("RESERVE"))),
    )
    if not output.strip().endswith("OK"):
        raise _h("CheckFailed")(f"generation runtime probe failed: {output.strip()[:300]}")


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
    except TimeoutError:
        raise
    except (OSError, ValueError):
        pass
    pengine = None
    if padapter is not None:
        try:
            value = json.loads(Path(_engine_path(padapter)).read_text(encoding="utf-8"))
            if isinstance(value, dict):
                pengine = value
        except TimeoutError:
            raise
        except (OSError, ValueError, FileNotFoundError):
            pass
    return padapter, pengine


def write_generation_configs(generation: Path, python: Path, adapter: dict) -> Path:
    current = _h("read_current")(adapter)
    active = _h("read_json")(Path(current['adapter_config']))
    if active is not None:
        adapter = active
    engine = load_engine_config(adapter)
    engine.pop("agent_command", None)
    padapter, pengine = _h("_profile_authority_sources")(adapter)
    if pengine is not None:
        for key in ("community_config", "admission_path"):
            value = pengine.get(key)
            if isinstance(value, str) and os.path.isabs(value):
                engine[key] = value
    config_dir = generation / "config"
    gen_engine = config_dir / "kimi.engine.json"
    gen_adapter = config_dir / "kimi.adapter.json"
    _h("atomic_write")(
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
        except TimeoutError:
            raise
        except (OSError, ValueError, FileNotFoundError):
            kimi_bin = None
    if isinstance(kimi_bin, str) and kimi_bin:
        gen_adapter_value["kimi_bin"] = kimi_bin
    git_bin = adapter.get("git_bin")
    if not (isinstance(git_bin, str) and git_bin):
        from paths import resolve_git_bin

        try:
            git_bin = resolve_git_bin()
        except TimeoutError:
            raise
        except (OSError, ValueError):
            git_bin = None
    if isinstance(git_bin, str) and git_bin:
        gen_adapter_value["git_bin"] = git_bin
    gen_adapter_value['domain_source_configs'] = dict(adapter.get('domain_configs') or {})
    from capture_config import prepare_domains
    gen_adapter_value['domain_configs'] = prepare_domains(
        python, gen_adapter_value, _h("read_json")(gen_engine), config_directory=str(config_dir / 'domains'),
        authority_directory=str(Path(profile).parent / 'knowledge-domains'),
        consent_config=str(Path(profile).with_name('mindie-consent.json')),
        public_feeds=bool(engine.get('feeds')))
    _h("atomic_write")(gen_adapter, gen_adapter_value)
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
    launcher_dir = _h("launch_dir")(adapter) / sha
    launcher_dir.mkdir(parents=True, exist_ok=True)
    launcher = launcher_dir / _h("LAUNCHER")
    for name in (_h("LAUNCHER"), "bounded.py", "diagnostic_support.py", "paths.py", "launch_budget.py"):
        if not (launcher_dir / name).is_file():
            shutil.copy2(generation / "scripts" / name, launcher_dir / name)
    for name in ("updater.py", "updater_check.py", "updater_schedule.py", "updater_staging.py"):
        source = generation / "scripts" / name
        if source.is_file() and not (launcher_dir / name).exists():
            shutil.copy2(source, launcher_dir / name)
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
        front = _h("_write_front_wrapper")(package, Path(base_python))
        front_env = None
    bound = ["--config", str(config_file)]
    for surface in manifest["mcpServers"]:
        previous = manifest["mcpServers"].get(surface)
        # Rebuild only the native launch fields; everything else the host
        # accepts (cwd, toolTimeoutMs, future host fields) stays byte-for-byte
        # as the candidate declared it. Dropping toolTimeoutMs silently
        # reverted the 660s remote deadline to the host's shorter default.
        entry = dict(previous) if isinstance(previous, dict) else {}
        env = dict(entry.get("env") or {})
        if front_env is not None:
            env.update(front_env)
        if surface == "remote":
            # The profile is a trusted plugin property, never selected by a
            # tool argument or inherited from an unrelated host process.
            env["REMOTE_DEV_HARNESS_PROFILE"] = REMOTE_PROFILE
            timeout = entry.get("toolTimeoutMs")
            if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
                entry["toolTimeoutMs"] = REMOTE_TOOL_TIMEOUT_MS
        entry["command"] = front
        entry["args"] = [str(launcher), *bound, "mcp", surface]
        if env:
            entry["env"] = env
        else:
            entry.pop("env", None)
        manifest["mcpServers"][surface] = entry
    quoted = _h("_quote_front_command")(base_python, launcher, config_file)
    for hook in manifest.get("hooks", []):
        if op := {"PreToolUse": "pretool", "Stop": "stop"}.get(hook.get("event")):
            hook["command"] = f"{quoted} hook {op}"
    for name in ("skills", "commands"):
        source = generation / name
        if source.is_dir():
            shutil.copytree(source, package / name)
    (package / "kimi.plugin.json").write_text(json.dumps(manifest, indent=2) + "\n")
    metadata = {"version": manifest["version"]}
    if re.fullmatch(_h("SHA"), sha):
        metadata["revision"] = sha
    for folder in (generation / "scripts", launcher_dir, package / "scripts"):
        path = folder / "diagnostic-build.json"
        if not path.exists():
            _h("atomic_write")(path, metadata)
    return package


def _host_package_metadata(package: Path) -> dict:
    path = Path(package) / "kimi.plugin.json"
    manifest = _h("read_json")(path) or {}
    servers = manifest.get("mcpServers") or {}
    remote = servers.get("remote") or {}
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "version": manifest.get("version"),
        "remote_timeout_ms": remote.get("toolTimeoutMs"),
        "remote_profile": (remote.get("env") or {}).get("REMOTE_DEV_HARNESS_PROFILE"),
        "bound_mcp_surfaces": sorted(
            name for name, server in servers.items()
            if server.get("args", [])[-2:] == ["mcp", name]
        ),
    }


def _mark_host_package(receipt_path: Path, package: Path) -> None:
    receipt = _h("read_json")(receipt_path)
    if not isinstance(receipt, dict):
        receipt = {}
    receipt["host_package"] = _h("_host_package_metadata")(package)
    _h("atomic_write")(receipt_path, receipt)


def _swap_host_package(staging: Path, destination: Path) -> Path | None:
    """Publish a fully built repair package and return the previous backup."""
    backup = destination.with_name(destination.name + ".repair-previous")
    if backup.exists():
        _h("_remove_owned_tree")(backup)
    if destination.exists():
        os.replace(destination, backup)
    try:
        os.replace(staging, destination)
    except Exception:
        if backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    return backup if backup.exists() else None


def _restore_host_package(destination: Path, backup: Path | None) -> None:
    if backup is None or not backup.exists():
        return
    if destination.exists():
        _h("_remove_owned_tree")(destination)
    os.replace(backup, destination)


def repair_host_package(adapter: dict, current: dict, deadline: float,
                        install, idle=None, lock_timeout: float = 30.0):
    """Rebuild and reinstall incomplete packages written by older updaters.

    0.1.2/0.1.3 receipts lack a `host_package` marker; later builders only bound
    knowledge/remote and left newly declared surfaces pointing at absent
    package scripts. A verified receipt records the surfaces actually bound
    to the stable launcher. The replacement is built
    in an inactive sibling first, then published under the same exclusive
    switch lock after an idle check. If install fails, the previous package is
    restored; the old generation tree and running host sessions are untouched.
    """
    sha = current.get("sha")
    if not isinstance(sha, str) or re.fullmatch(_h("SHA"), sha) is None:
        return None
    receipt_path = _h("receipts_dir")(adapter) / f"{sha}.json"
    receipt = _h("read_json")(receipt_path)
    if not isinstance(receipt, dict) or receipt.get("sha") != sha:
        return None
    generation = Path(current["generation"])
    if not (generation / "kimi.plugin.json").is_file():
        return None
    source_manifest = _h("read_json")(generation / "kimi.plugin.json")
    marker = receipt.get("host_package") or {}
    if marker.get("bound_mcp_surfaces") == sorted(source_manifest["mcpServers"]):
        return None
    destination = generation / "host-package"
    staging = generation / f".host-package-repair-{os.getpid()}"
    if staging.exists():
        _h("_remove_owned_tree")(staging)
    stopped = False
    try:
        package = _h("build_host_package")(
            generation, adapter, sha, package_dir=staging,
            config_file=Path(current["adapter_config"]),
        )
        wait = min(lock_timeout, deadline - time.monotonic())
        if wait <= 0:
            raise _h("LockTimeout")("no time left for the native package repair")
        with _h("OperationLock")(adapter).exclusive(timeout=wait):
            active = _h("read_json")(_h("current_path")(adapter)) or {}
            if (active.get("sha") != sha
                    or active.get("generation") != str(generation)):
                return None
            if idle is not None:
                idle_result = idle(adapter, current, deadline)
                if (isinstance(idle_result, dict)
                        and type(idle_result.get("idle")) is bool
                        and idle_result["idle"] is False):
                    raise _h("Deferred")("knowledge service is active; package repair deferred")
                stopped = (isinstance(idle_result, dict)
                           and idle_result.get("service") == "stopped")
            backup = _h("_swap_host_package")(package, destination)
            try:
                install(adapter, destination, deadline,
                        reserve=_h("FEED_BUDGET") + _h("HANDOFF_BUDGET"))
            except Exception:
                _h("_restore_host_package")(destination, backup)
                # Restoring source JSON is not enough: Kimi registers parsed
                # MCP/hook definitions. Re-apply the previous package through
                # the existing native install/readback and only then permit
                # stopped-service restoration. If rollback is unproven, keep
                # the explicit failure and do not claim the old service is
                # safely restored.
                restored = _h("_rollback_native")(adapter, current, install, deadline)
                if stopped:
                    _h("_restore_stopped_service")(
                        adapter, current if restored else None, deadline,
                    )
                    stopped = False
                raise
            if backup is not None:
                _h("_remove_owned_tree")(backup)
            _h("_mark_host_package")(receipt_path, destination)
            if stopped:
                _h("_restore_stopped_service")(adapter, current, deadline)
                stopped = False
    finally:
        if staging.exists():
            _h("_remove_owned_tree")(staging)
    return destination


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
    build=None, release=None,
) -> tuple[Path, Path, Path]:
    build = build or _h("build_runtime")
    target = _h("generations_dir")(adapter) / sha
    if (target / _h("COMPLETE")).is_file():
        if release is not None:
            _h("_verify_generation_release")(target, sha, release)
        python = _h("_venv_python")(target / ".venv")
        gen_adapter = target / "config" / "kimi.adapter.json"
        if python.exists() and gen_adapter.is_file():
            current = _h("read_current")(adapter)
            active, candidate = _h("read_json")(Path(current['adapter_config'])), _h("read_json")(gen_adapter)
            if (current.get('sha') != sha and active is not None and candidate is not None
                    and candidate.get('domain_source_configs') != (active.get('domain_configs') or {})):
                # A refreshed but unpublished generation is complete only
                # after both its binding and host package were rebuilt.
                (target / _h("COMPLETE")).unlink()
                gen_adapter = _h("write_generation_configs")(target, python, active)
                _h("build_host_package")(target, active, sha, config_file=gen_adapter)
                (target / _h("COMPLETE")).write_text(f"{sha}\n")
            return target, python, gen_adapter
        _h("_remove_owned_tree")(target)
    elif target.exists():
        _h("_remove_owned_tree")(target)
    staging = _h("generations_dir")(adapter) / f".staging-{sha}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _h("_git")(["init", "-q", str(staging)], timeout=30, deadline=deadline)
        _h("_git")(["remote", "add", "origin", remote], timeout=15, deadline=deadline, cwd=staging)
        _h("_git")(["fetch", "--depth", "1", "origin", sha], timeout=180, deadline=deadline, cwd=staging, transport=True)
        _h("_git")(["checkout", "-q", "--detach", "FETCH_HEAD"], timeout=60, deadline=deadline, cwd=staging)
        os.replace(staging, target)
    except TimeoutError:
        _h("_remove_owned_tree")(staging)
        raise
    except Exception:
        _h("_remove_owned_tree")(staging)
        raise
    try:
        if release is not None:
            _h("_verify_generation_release")(target, sha, release)
        python = build(target, deadline)
        _h("probe_runtime")(python, deadline, target)
        gen_adapter = _h("write_generation_configs")(target, python, adapter)
        _h("build_host_package")(target, adapter, sha, config_file=gen_adapter)
        (target / _h("COMPLETE")).write_text(f"{sha}\n")
        return target, python, gen_adapter
    except TimeoutError:
        _h("_remove_owned_tree")(target)
        raise
    except Exception:
        _h("_remove_owned_tree")(target)
        raise


def _verify_generation_release(generation: Path, sha: str, release: dict) -> None:
    from release_metadata import metadata

    if metadata(generation, sha) != release:
        raise _h("CheckFailed")("fetched source does not match the formal Release metadata")
