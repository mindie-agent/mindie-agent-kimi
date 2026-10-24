#!/usr/bin/env python3
"""Candidate-generation runtime and host-package staging for the updater.

[Role]
Pins and probes a candidate generation's runtime, materializes generation
adapter/engine configs, builds the native host package with the versioned
launcher, and stages or removes owned generation trees. `updater.py` re-exports
these helpers and binds itself so monkeypatches keep resolving through it.

[Invariants]
- A generation is only marked complete after runtime probe, config write, and
  host-package build all succeed; any failure removes the owned tree.
- Published launchers are versioned under `update/launch/<sha>` and never
  mutate a live generation.
- Host MCP commands remain native-valid (literal `python3`, or `./mindie-front`).

[Timeouts & Locks]
- All subprocess work is bounded by the caller deadline with `RESERVE` held
  back; no module-level lock is acquired here.

[Side-Effect Policy]
- Writes only under `update/generations/<sha>`, `update/launch/<sha>`, and the
  caller-provided host-package directory; removal only targets owned trees.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import sys
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
    if not launcher.is_file():
        for name in (_h("LAUNCHER"), "bounded.py", "diagnostic_support.py", "paths.py"):
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
    for surface in ("knowledge", "remote"):
        previous = manifest["mcpServers"].get(surface)
        env = dict(previous.get("env") or {}) if isinstance(previous, dict) else {}
        if front_env is not None:
            env.update(front_env)
        entry = {
            "command": front,
            "args": [str(launcher), *bound, "mcp", surface],
        }
        if env:
            entry["env"] = env
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
