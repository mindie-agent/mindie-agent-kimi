#!/usr/bin/env python3
"""[Role] Thin setup CLI: `init`/`install` write `kimi.json` and provision all 9 public
domain engines via `mindie_harness.runtime.knowledge_binding`; `sharing`, `doctor`, and
`update` delegate to `mindie_harness` (`API_VERSION = 1`).
[Invariants] `ensure_knowledge_binding(..., already_locked=True)` must return `status == "configured"`
before `kimi.json` is written atomically under `.kimi.json.update.lock`; legacy `MindIEKimiUpdate`
is queried/deleted before unlinking wrappers; `release-metadata` validates `0.1.33` + AST targets.
[Timeouts & Locks] `.update.lock` (`5s`) and `schtasks` (`15s`); zero network I/O on `init`.
[Side-Effect Policy] Writes `kimi.json` + 9 domain engine configs, retires legacy schedules,
and prints one JSON object per command.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mindie_harness.paths import ProfileRef

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
PLUGIN_ROOT = HERE.parent
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_NO_WIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
VERSION = "0.1.33"
DOMAIN = "vllm-ascend"
HARNESS = "kimi-code"
REPOSITORY = "mindie-agent/mindie-agent-kimi"
LEGACY_TASK_NAME = "MindIEKimiUpdate"
COMMANDS = frozenset({"init", "install", "sharing", "doctor", "update", "release-metadata"})

def _runtime_python() -> str | None:
    try:
        return sys.executable if importlib.util.find_spec("mindie_harness") is not None else None
    except (ImportError, ValueError):
        return None

def _unlink_legacy_wrappers(profile: ProfileRef) -> list[str]:
    removed: list[str] = []
    for folder in (profile.state_dir, profile.state_dir / "update"):
        for ext in (".pyw", ".xml"):
            if (target := folder / f"{LEGACY_TASK_NAME}{ext}").is_file():
                try:
                    target.unlink(missing_ok=True); removed.append(str(target))
                except TimeoutError:
                    raise
                except OSError:
                    pass
    return removed

def _retire_legacy_schedule(profile: ProfileRef, *, os_name: str = os.name, platform_name: str = sys.platform) -> dict[str, object]:
    if os.environ.get("MINDIE_KIMI_SKIP_SCHTASKS") == "1":
        return {"status": "skipped", "task": LEGACY_TASK_NAME, "removed_files": _unlink_legacy_wrappers(profile)}
    if os_name == "nt":
        try:
            q = subprocess.run(["schtasks", "/Query", "/TN", LEGACY_TASK_NAME], stdin=subprocess.DEVNULL, capture_output=True, timeout=15.0, check=False, creationflags=_NO_WIN)
            if q.returncode != 0:
                denied = b"access is denied" in (q.stderr or b"").lower()
                return {"status": "failed" if denied else "absent", "task": LEGACY_TASK_NAME, "removed_files": [] if denied else _unlink_legacy_wrappers(profile)}
            d = subprocess.run(["schtasks", "/Delete", "/TN", LEGACY_TASK_NAME, "/F"], stdin=subprocess.DEVNULL, capture_output=True, timeout=15.0, check=False, creationflags=_NO_WIN)
            return {"status": "retired" if d.returncode == 0 else "failed", "task": LEGACY_TASK_NAME, "removed_files": _unlink_legacy_wrappers(profile) if d.returncode == 0 else []}
        except (TimeoutError, subprocess.TimeoutExpired):
            raise
        except OSError:
            return {"status": "skipped", "task": LEGACY_TASK_NAME, "removed_files": []}
    if platform_name == "darwin" and (plist := Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist").is_file():
        try:
            from mindie_harness.runtime.scheduler import retire_schedule
            return retire_schedule(name="agent.mindie.kimi-update", plist_paths=(plist,))
        except (TimeoutError, subprocess.TimeoutExpired):
            raise
        except Exception:
            return {"status": "failed", "task": "agent.mindie.kimi-update", "removed_files": []}
    if platform_name.startswith("linux"):
        sdir = Path(os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")) / "systemd" / "user"
        if (sdir / "mindie-kimi-update.timer").is_file() or (sdir / "mindie-kimi-update.service").is_file():
            try:
                from mindie_harness.runtime.scheduler import retire_schedule
                return retire_schedule(name="mindie-kimi-update", systemd_dir=sdir)
            except (TimeoutError, subprocess.TimeoutExpired):
                raise
            except Exception:
                return {"status": "failed", "task": "mindie-kimi-update", "removed_files": []}
    return {"status": "absent", "task": LEGACY_TASK_NAME, "removed_files": _unlink_legacy_wrappers(profile)}

def _bind_locked(profile: ProfileRef, *, python: str | None, root: Path | None = None) -> tuple[dict, dict]:
    from mindie_harness.atomic import atomic_write_json, read_json_safe
    from mindie_harness.locks import FileLock
    from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding

    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    prev_env = os.environ.get("MINDIE_KNOWLEDGE_ROOT")
    try:
        if root is not None:
            os.environ["MINDIE_KNOWLEDGE_ROOT"] = str(root.expanduser().resolve(strict=False))
        bound = ensure_knowledge_binding(profile, plugin_root=PLUGIN_ROOT, python_executable=python, install_redactor=False, retire_previous=True, include_default_feed=True, already_locked=True)
        if bound.get("status") != "configured":
            raise SystemExit(f"knowledge binding {bound.get('status')}: {bound.get('reason') or 'failed'}")
        prev = read_json_safe(profile.config_path)
        adapter = {**(prev if isinstance(prev, dict) else {}), "state_dir": str(profile.state_dir), "profile_config": str(profile.config_path), "source_root": str(PLUGIN_ROOT)}
        if python:
            adapter["python"] = python
        if home := os.environ.get("KIMI_CODE_HOME"):
            adapter["kimi_home"] = str(Path(home).expanduser())
        adapter.update(bound["binding_fields"])
        adapter["updated_at"] = time.time()
        atomic_write_json(profile.config_path, adapter)
        return adapter, bound
    finally:
        if root is not None:
            os.environ.pop("MINDIE_KNOWLEDGE_ROOT", None) if prev_env is None else os.environ.__setitem__("MINDIE_KNOWLEDGE_ROOT", prev_env)
        lock.release()

def _init(profile: ProfileRef, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="setup.py init")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--domain", default=None)
    opts = parser.parse_args(argv)
    _retire_legacy_schedule(profile)
    adapter, bound = _bind_locked(profile, python=_runtime_python(), root=opts.root)
    domain, routes = opts.domain or DOMAIN, dict(bound.get("domain_configs") or {})
    print(json.dumps({"status": "initialized", "config_path": str(profile.config_path), "engine_config": str(routes.get(domain) or bound["engine_config"]), "domain_configs": routes, "native_package": str(PLUGIN_ROOT), "domain": domain, "python": adapter.get("python")}, sort_keys=True))
    return 0

def _sharing(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.sharing import configure_sharing, sharing_status

    parser = argparse.ArgumentParser(prog="setup.py sharing")
    group = parser.add_mutually_exclusive_group()
    for flag in ("--status", "--enable", "--local-only", "--disable"):
        group.add_argument(flag, action="store_true")
    parser.add_argument("--project-root", action="append", default=[])
    for opt in ("--repository", "--fork", "--account"):
        parser.add_argument(opt)
    opts = parser.parse_args(argv)
    if opts.status or not (opts.enable or opts.local_only or opts.disable):
        print(json.dumps(sharing_status(profile, cwd=Path.cwd()), sort_keys=True)); return 0
    choice = "contribute" if opts.enable else "local-only" if opts.local_only else "disabled"
    print(json.dumps(configure_sharing(profile, choice=choice, project_roots=opts.project_root, repository=opts.repository, fork=opts.fork, account=opts.account), sort_keys=True))
    return 0

def _doctor(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.api import run_host

    parser = argparse.ArgumentParser(prog="setup.py doctor")
    parser.add_argument("--probe-remote", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--timeout", type=float)
    opts = parser.parse_args(argv)
    cmd = ["--host", "kimi", "--plugin-root", str(PLUGIN_ROOT), "--config", str(profile.config_path), "doctor"]
    cmd += (["--probe-remote"] if opts.probe_remote else []) + (["--cwd", opts.cwd] if opts.cwd else []) + (["--timeout", str(opts.timeout)] if opts.timeout is not None else [])
    return run_host(cmd)

def _update(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime

    parser = argparse.ArgumentParser(prog="setup.py update")
    for flag in ("--force", "--offline", "--background"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--channel-base-url")
    opts = parser.parse_args(argv)
    _retire_legacy_schedule(profile)
    outcome = update_runtime(profile, force=opts.force, offline=opts.offline, channel_base_url=opts.channel_base_url, background=opts.background)
    if outcome.get("status") in {"active", "up_to_date"}:
        _bind_locked(profile, python=outcome.get("python") if isinstance(outcome.get("python"), str) else _runtime_python())
    print(json.dumps(outcome, sort_keys=True))
    return 0

def _declares(path: Path, parts: list[str]) -> bool:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    for part in parts:
        if (node := next((item for item in body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part), None)) is None:
            return False
        body = node.body
    return True

def _target(raw: str) -> tuple[str, list[str]]:
    if "::" in raw:
        parts = raw.split("::"); return parts[0], [p for p in parts[1:] if p]
    path, _, symbol = raw.partition(":")
    return path, [symbol] if symbol else []

def _release_metadata(argv: list[str]) -> int:
    from mindie_knowledge.releases import validate_compatibility, validate_release_metadata

    parser = argparse.ArgumentParser(prog="setup.py release-metadata")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tests", type=Path, default=None)
    opts = parser.parse_args(argv)
    source = opts.source.expanduser().resolve()
    declaration = validate_compatibility(json.loads((source / "harness-compatibility.json").read_text(encoding="utf-8-sig")), harness_name=HARNESS)
    manifest = json.loads((source / "kimi.plugin.json").read_text(encoding="utf-8-sig"))
    problems = [f"kimi.plugin.json version={manifest.get('version')!r}"] if manifest.get("version") != VERSION else []
    for entry in declaration["interface_registry"].values():
        for raw in list(entry["code_paths"]) + (list(entry["test_ids"]) if opts.tests is not None else []):
            relative, parts = _target(raw)
            path = source / relative
            if not path.is_file() or (path.suffix == ".py" and parts and not _declares(path, parts)):
                problems.append(raw)
    if problems:
        raise SystemExit("unresolved release metadata targets: " + "; ".join(sorted(set(problems))))
    print(json.dumps(validate_release_metadata({"schema": "mindie-plugin-release/1", "version": manifest["version"], "source": {"repository": REPOSITORY, "sha": opts.revision}, "compatibility": declaration}, repository=REPOSITORY, harness_name=HARNESS), sort_keys=True))
    return 0

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args, config = list(raw_args), None
    while "--config" in args:
        idx = args.index("--config")
        if idx + 1 >= len(args):
            raise SystemExit("--config requires a path")
        config, args = args[idx + 1], args[:idx] + args[idx + 2:]
    if _runtime_python() is None:
        import mindie_launch
        cfg_path = Path(config).expanduser().absolute() if config else mindie_launch._config_path()
        if (py := mindie_launch._active_python(cfg_path)) is not None and py.resolve() != Path(sys.executable).resolve():
            return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=_NO_WIN).returncode
    command = args[0] if args and args[0] in COMMANDS else "init"
    rest = args[1:] if args and args[0] in COMMANDS else args
    if command == "release-metadata":
        return _release_metadata(rest)
    from mindie_harness.paths import ProfileRef
    profile = ProfileRef.resolve("kimi", plugin_root=PLUGIN_ROOT, config_path=config)
    return _init(profile, rest) if command in {"init", "install"} else {"sharing": _sharing, "doctor": _doctor, "update": _update}[command](profile, rest)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
