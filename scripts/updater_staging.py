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
import tempfile
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
SOURCE = ".mindie-generation-source"
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


def _stage_failure(exc, stage):
    """Preserve the precise subprocess stage/reason on a propagated failure."""
    try:
        if not getattr(exc, "stage", None):
            exc.stage = stage
        if not getattr(exc, "reason_code", None):
            exc.reason_code = stage + "_failed"
    except (AttributeError, TypeError):
        pass
    return exc


def _stage_call(stage, function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except TimeoutError:
        raise
    except Exception as exc:
        _stage_failure(exc, stage)
        raise


def _component_override_file(generation: Path, overrides: dict) -> Path:
    from mindie_knowledge.runtime_inventory import COMPONENTS, SHA

    lines = []
    for name in sorted(overrides):
        revision = overrides[name]
        if (name not in COMPONENTS or not isinstance(revision, str)
                or SHA.fullmatch(revision) is None):
            raise _h("CheckFailed")("candidate component floor is invalid")
        lines.append(f"{name} @ git+https://github.com/{COMPONENTS[name]}@{revision}")
    handle, name = tempfile.mkstemp(
        prefix=".component-overrides-", suffix=".txt", dir=str(generation.parent))
    with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    return Path(name)


def _verify_component_floor(python: Path, overrides: dict, deadline: float) -> None:
    from mindie_knowledge.component_runtime import read_inventory

    installed = read_inventory(
        python, timeout=min(15, _h("_remaining")(deadline, _h("RESERVE"))))
    for name, revision in overrides.items():
        if installed.get(name, {}).get("revision") != revision:
            raise _h("CheckFailed")(
                f"candidate runtime did not retain the component floor: {name}")


def build_runtime(generation: Path, deadline: float, *, component_overrides=None) -> Path:
    """Build the candidate venv in one pass at the merged component floor.

    `component_overrides` carries the already-resolved `{name: revision}` floor
    when the active runtime is newer than the candidate's static
    `active-overrides.txt`; the candidate is then installed with exactly those
    pins and its resulting inventory is verified before the build is reported.
    """
    if sys.version_info < _h("MIN_PYTHON"):
        raise _h("CheckFailed")(f"Python {_h('MIN_PYTHON')[0]}.{_h('MIN_PYTHON')[1]}+ is required")
    requirements = generation / "runtime-requirements.txt"
    overrides = generation / "runtime/active-overrides.txt"
    if not overrides.is_file():
        overrides = requirements
    if re.search(r"@main\b", requirements.read_text(encoding="utf-8")):
        raise _h("CheckFailed")("runtime-requirements.txt must pin full commit SHAs, not @main")
    venv = generation / ".venv"
    python = _h("_venv_python")(venv)
    from paths import which_host_binary
    uv = which_host_binary("uv", which_fn=shutil.which)
    from setup import installer_environment

    if uv is None:
        raise _h("CheckFailed")("uv is required to prepare the MindIE runtime")
    env = installer_environment()
    from mindie_knowledge.storage import configure_same_volume_uv_cache

    env = configure_same_volume_uv_cache(generation, env)
    _stage_call(
        "candidate_venv", _h("bounded_run"),
        [uv, "venv", "--python", sys.executable, str(venv)], "",
        timeout=min(180, _h("_remaining")(deadline, _h("RESERVE"))), env=env,
        transport=True)
    merged = None
    if component_overrides:
        merged = _component_override_file(generation, component_overrides)
        overrides = merged
    install_argv = [uv, "pip", "install", "--python", str(python), "-r", str(requirements)]
    if overrides != requirements:
        install_argv += ["-r", str(overrides)]
    install_argv += ["--overrides", str(overrides)]
    try:
        _stage_call(
            "candidate_runtime", _h("bounded_run"), install_argv, "",
            timeout=min(600, _h("_remaining")(deadline, _h("RESERVE"))),
            max_output=512 * 1024, env=env, transport=True,
        )
        if merged is not None:
            _verify_component_floor(python, component_overrides, deadline)
    finally:
        if merged is not None:
            try:
                merged.unlink(missing_ok=True)
            except TimeoutError:
                raise
            except OSError:
                pass
    return python


def probe_runtime(python: Path, deadline: float, generation: Path) -> None:
    from setup import probe_script

    parser = str(generation / "scripts" / "transcript.py")
    output = _stage_call(
        "runtime_probe", _h("bounded_run"),
        [str(python), "-c", probe_script(parser)], "",
        timeout=min(60, _h("_remaining")(deadline, _h("RESERVE"))),
    )
    if not output.strip().endswith("OK"):
        error = _h("CheckFailed")(f"generation runtime probe failed: {output.strip()[:300]}")
        error.reason_code = "runtime_probe_failed"
        raise _stage_failure(error, "runtime_probe")


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


def write_generation_configs(generation: Path, python: Path, adapter: dict, *, components=None) -> Path:
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
    pdoc = adapter if padapter is None else padapter
    raw_cmds = pdoc.get("component_commands")
    commands = dict(raw_cmds) if isinstance(raw_cmds, dict) else {}
    if components is not None:
        from mindie_knowledge.component_bindings import rebind_commands, validate_command_floor
        validate_command_floor(pdoc, components)
        # A binding-only update must not rewrite the active generation's config;
        # a fresh candidate keeps its stable `config/kimi.adapter.json` so a
        # retry can reuse the completed generation.
        if _same_path(generation, Path(current["generation"])):
            config_dir /= hashlib.sha256(
                json.dumps(commands, sort_keys=True).encode()).hexdigest()[:16]
    gen_engine = config_dir / "kimi.engine.json"
    gen_adapter = config_dir / "kimi.adapter.json"
    _h("atomic_write")(gen_engine, dict(
        engine, transcript_adapter=str(generation / "scripts" / "transcript.py"),
        **__import__("capture_config").prepare(python, generation / "scripts")))
    profile = pdoc.get("profile_config") or adapter.get("profile_config")
    if not (isinstance(profile, str) and os.path.isabs(profile)):
        from paths import profile_config_path
        profile = str(profile_config_path())
    gen_adapter_value = dict(
        adapter, python=str(python), engine_config=str(gen_engine), profile_config=profile)
    if components is not None:
        gen_adapter_value["component_source_commands"] = commands
        gen_adapter_value["component_commands"] = rebind_commands(pdoc, python, components)
    else:
        gen_adapter_value.pop("component_source_commands", None)
        if not commands:
            gen_adapter_value.pop("component_commands", None)
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
    for name in (
        _h("LAUNCHER"), "bounded.py", "diagnostic_support.py", "paths.py", "launch_budget.py",
        "kimi_paths.py", "kimi_bounded.py", "kimi_diagnostics.py", "kimi_launch.py",
    ):
        src_file = generation / "scripts" / name
        if src_file.is_file() and not (launcher_dir / name).is_file():
            shutil.copy2(src_file, launcher_dir / name)
    transport = generation / "scripts/native_mcp.py"
    if transport.is_file() and not (launcher_dir / transport.name).exists():
        shutil.copy2(transport, launcher_dir / transport.name)
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
    configured = _h("read_json")(config_file) or {}
    base_python = configured.get("python") or sys.executable
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


def _same_path(first, second):
    from mindie_knowledge.filesystem import path_identity

    try:
        return path_identity(Path(first)) == path_identity(Path(second))
    except TimeoutError:
        raise
    except (OSError, RuntimeError, ValueError):
        return os.path.normcase(os.path.abspath(str(first))) == os.path.normcase(
            os.path.abspath(str(second)))


def _usable_python(current):
    value = current.get("python") if isinstance(current, dict) else None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    try:
        return path if path.is_file() else None
    except TimeoutError:
        raise
    except OSError:
        return None


def _discard_quietly(path):
    try:
        _h("_remove_owned_tree")(path)
    except TimeoutError:
        raise
    except Exception:
        pass


def _discard_unpublished(sha, target, adapter, launch_existed):
    """Remove only candidate artifacts this failed staging may have created."""
    try:
        current = _h("read_current")(adapter)
    except TimeoutError:
        raise
    except Exception:
        current = {}
    try:
        published = (target / _h("COMPLETE")).is_file()
    except TimeoutError:
        raise
    except OSError:
        published = False
    active = current.get("generation")
    if target.exists() and not published and not (
            isinstance(active, str) and _same_path(active, target)):
        _discard_quietly(target)
    launch = _h("launch_dir")(adapter) / sha
    if (not launch_existed and current.get("sha") != sha):
        try:
            exists = launch.exists()
        except TimeoutError:
            raise
        except OSError:
            exists = False
        if exists:
            _discard_quietly(launch)


def _reuse_complete(target, sha, adapter, release, deadline):
    if release is not None:
        _h("_verify_generation_release")(target, sha, release)
    python = _h("_venv_python")(target / ".venv")
    gen_adapter = target / "config" / "kimi.adapter.json"
    if not python.exists() or not gen_adapter.is_file():
        _h("_remove_owned_tree")(target)
        return None
    current = _h("read_current")(adapter)
    active, candidate = _h("read_json")(Path(current["adapter_config"])), _h("read_json")(gen_adapter)
    from component_update import candidate_commands_stale, candidate_components
    if (current.get("sha") != sha and active is not None and candidate is not None
            and (candidate.get("domain_source_configs") != (active.get("domain_configs") or {})
                 or candidate_commands_stale(active, candidate))):
        # A refreshed but unpublished generation is complete only after both
        # its binding and host package were rebuilt.
        (target / _h("COMPLETE")).unlink()
        gen_adapter = _h("write_generation_configs")(
            target, python, active,
            components=candidate_components(active, python, deadline))
        _h("build_host_package")(target, active, sha, config_file=gen_adapter)
        (target / _h("COMPLETE")).write_text(f"{sha}\n")
    return target, python, gen_adapter


def _stage_checkout(sha, remote, adapter, deadline):
    staging = _h("generations_dir")(adapter) / f".staging-{sha}-{os.getpid()}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        _h("_git")(["init", "-q", str(staging)], timeout=30, deadline=deadline)
        _h("_git")(["remote", "add", "origin", remote], timeout=15, deadline=deadline, cwd=staging)
        _stage_call(
            "stage_checkout", _h("_git"),
            ["fetch", "--depth", "1", "origin", sha], timeout=180, deadline=deadline,
            cwd=staging, transport=True)
        _h("_git")(["checkout", "-q", "--detach", "FETCH_HEAD"], timeout=60, deadline=deadline, cwd=staging)
    except TimeoutError:
        _discard_quietly(staging)
        raise
    except Exception:
        _discard_quietly(staging)
        raise
    target = _h("generations_dir")(adapter) / sha
    os.replace(staging, target)
    return target


def _finalize_target(sha, target, adapter, deadline, build, force):
    """Build exactly one runtime: a merged component runtime wins over a venv."""
    current = _h("read_current")(adapter)
    usable = _usable_python(current)
    from component_update import candidate_components, candidate_overrides
    from component_update import prepare as component_prepare

    def candidate_config(python):
        # `prepare()` returning `None` proves the active interpreter already
        # carries the merged floor, not that the candidate needs no binding:
        # its managed commands must still point at the candidate `.venv`.
        # Inheriting the active generation's commands defers the switch CAS
        # ("component commands changed during preparation") and every retry
        # then reuses the same broken candidate.
        return _h("write_generation_configs")(
            target, python, adapter,
            components=candidate_components(adapter, python, deadline))

    if usable is None:
        python = build(target, deadline)
        _h("probe_runtime")(python, deadline, target)
        prepared = component_prepare(adapter, target, python, deadline, force=force)
        if prepared is not None and not _same_path(prepared[0], target):
            generation, python, config = prepared
            _h("build_host_package")(generation, adapter, sha, config_file=config)
            return generation, python, config
        config = (prepared[2] if prepared is not None
                  else candidate_config(python))
        _h("build_host_package")(target, adapter, sha, config_file=config)
        (target / _h("COMPLETE")).write_text(f"{sha}\n")
        return target, python, config
    prepared = component_prepare(adapter, target, usable, deadline, force=force)
    if prepared is not None and not _same_path(prepared[0], target):
        # A merged component runtime already owns a complete .venv; do not
        # also materialize generations/<sha>/.venv for the same update.
        generation, python, config = prepared
        _h("build_host_package")(generation, adapter, sha, config_file=config)
        return generation, python, config
    # `prepare` returning `None` (or a binding-only result) proves the active
    # interpreter already carries the merged floor. A fresh candidate venv
    # built from the candidate's static active-overrides.txt alone would
    # downgrade those components, so pin that resolved floor in the one build.
    floor = candidate_overrides(
        target, usable, timeout=min(15, _h("_remaining")(deadline, _h("RESERVE"))))
    python = (build(target, deadline, component_overrides=floor)
              if floor else build(target, deadline))
    _h("probe_runtime")(python, deadline, target)
    if prepared is not None:
        # The first pass could prove only a command-binding change against the
        # active runtime. Re-evaluate against the candidate interpreter; a
        # component build is still allowed if its inventory differs.
        prepared = component_prepare(adapter, target, python, deadline, force=force)
        if prepared is not None and not _same_path(prepared[0], target):
            generation, python, config = prepared
            _h("build_host_package")(generation, adapter, sha, config_file=config)
            return generation, python, config
        config = (prepared[2] if prepared is not None
                  else candidate_config(python))
    else:
        config = candidate_config(python)
    _h("build_host_package")(target, adapter, sha, config_file=config)
    (target / _h("COMPLETE")).write_text(f"{sha}\n")
    try:
        (target / _h("SOURCE")).unlink(missing_ok=True)
    except TimeoutError:
        raise
    except OSError:
        pass
    return target, python, config


def stage_generation(
    sha: str, remote: str, adapter: dict, deadline: float,
    build=None, release=None, force=False,
) -> tuple[Path, Path, Path]:
    build = build or _h("build_runtime")
    target = _h("generations_dir")(adapter) / sha
    launch_existed = (_h("launch_dir")(adapter) / sha).exists()
    try:
        if (target / _h("COMPLETE")).is_file():
            reused = _reuse_complete(target, sha, adapter, release, deadline)
            if reused is not None:
                return reused
        elif (target / _h("SOURCE")).is_file():
            if release is not None:
                _h("_verify_generation_release")(target, sha, release)
        elif target.exists():
            _h("_remove_owned_tree")(target)
        if not target.exists():
            _stage_checkout(sha, remote, adapter, deadline)
            if release is not None:
                _h("_verify_generation_release")(target, sha, release)
            (target / _h("SOURCE")).write_text(f"{sha}\n")
        return _finalize_target(sha, target, adapter, deadline, build, force)
    except TimeoutError:
        _discard_unpublished(sha, target, adapter, launch_existed)
        raise
    except Exception:
        _discard_unpublished(sha, target, adapter, launch_existed)
        raise


def _verify_generation_release(generation: Path, sha: str, release: dict) -> None:
    from release_metadata import metadata

    if metadata(generation, sha) != release:
        raise _h("CheckFailed")("fetched source does not match the formal Release metadata")
