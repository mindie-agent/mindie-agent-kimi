#!/usr/bin/env python3
"""[Role] Thin setup CLI: `init`/`install` write `kimi.json` and `kimi.engine.json`;
`sharing`, `doctor`, and `update` delegate to `mindie_harness` (`API_VERSION = 1`).
[Invariants] `kimi.engine.json` carries `root`, `domain`, `admission_path`, and an absolute
`transcript_adapter` -> `kimi_transcript.py`; `release-metadata` validates
`harness-compatibility.json` (`harness_name="kimi-code"`), the `0.1.32` manifest version,
every AST `code_paths` symbol, and every `test_ids` target when `--tests` is set; native
registration belongs to `install_kimi_plugin.py`, never `~/.kimi-code` mutation here.
[Timeouts & Locks] Init performs two atomic JSON writes and zero network I/O; `doctor`,
`sharing`, and `update` inherit harness-owned budgets.
[Side-Effect Policy] Writes the two profile documents (plus consent/community policy through
`mindie_harness.sharing`) and prints one JSON object per command.
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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mindie_harness.paths import ProfileRef

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
PLUGIN_ROOT = HERE.parent
_NO_WIN = 0x08000000 if os.name == "nt" else 0
VERSION = "0.1.32"
DOMAIN = "vllm-ascend"
HARNESS = "kimi-code"
REPOSITORY = "mindie-agent/mindie-agent-kimi"
COMMANDS = frozenset({"init", "install", "sharing", "doctor", "update", "release-metadata"})

def _runtime_python() -> str | None:
    try:
        return sys.executable if importlib.util.find_spec("mindie_harness") is not None else None
    except (ImportError, ValueError):
        return None

def _init(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.atomic import atomic_write_json, read_json_safe
    from mindie_harness.locks import FileLock

    parser = argparse.ArgumentParser(prog="setup.py init")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--domain", default=None)
    opts = parser.parse_args(argv)
    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    try:
        engine_path = profile.config_path.with_suffix(".engine.json")
        prev_engine = read_json_safe(engine_path)
        prev_engine = prev_engine if isinstance(prev_engine, dict) else {}
        prev_adapter = read_json_safe(profile.config_path)
        prev_adapter = prev_adapter if isinstance(prev_adapter, dict) else {}
        root = opts.root.expanduser().absolute() if opts.root else Path(prev_engine.get("root") or (profile.data_dir / "kimi"))
        domain = opts.domain or prev_engine.get("domain") or DOMAIN
        engine = {
            **prev_engine, "root": str(root), "domain": domain,
            "admission_path": str(prev_engine.get("admission_path") if not opts.root and prev_engine.get("admission_path") else root / "admission.sqlite3"),
            "capture_mode": "public-transcript", "transcript_adapter": str(HERE / "kimi_transcript.py"),
            "redactor_executable": os.environ.get("MINDIE_REDACTOR_EXECUTABLE") or prev_engine.get("redactor_executable") or str(root / "bin" / "gitleaks"),
        }
        adapter = {
            **prev_adapter, "engine_config": str(engine_path),
            "community_config": str(profile.config_path.parent / "mindie-community.json"),
            "consent_config": str(profile.consent_path), "state_dir": str(profile.state_dir),
            "profile_config": str(profile.config_path), "source_root": str(PLUGIN_ROOT),
        }
        if python := _runtime_python():
            adapter["python"] = python
        if home := os.environ.get("KIMI_CODE_HOME"):
            adapter["kimi_home"] = str(Path(home).expanduser())
        atomic_write_json(engine_path, engine)
        atomic_write_json(profile.config_path, adapter)
    finally:
        lock.release()
    print(json.dumps({
        "status": "initialized", "config_path": str(profile.config_path),
        "engine_config": str(engine_path), "native_package": str(PLUGIN_ROOT),
        "domain": domain, "python": adapter.get("python"),
    }, sort_keys=True))
    return 0

def _sharing(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.sharing import configure_sharing, sharing_status

    parser = argparse.ArgumentParser(prog="setup.py sharing")
    group = parser.add_mutually_exclusive_group()
    for flag in ("--status", "--enable", "--local-only", "--disable"):
        group.add_argument(flag, action="store_true")
    parser.add_argument("--project-root", action="append", default=[])
    parser.add_argument("--repository")
    parser.add_argument("--fork")
    parser.add_argument("--account")
    opts = parser.parse_args(argv)
    if opts.status or not (opts.enable or opts.local_only or opts.disable):
        print(json.dumps(sharing_status(profile, cwd=Path.cwd()), sort_keys=True))
        return 0
    choice = "contribute" if opts.enable else "local-only" if opts.local_only else "disabled"
    print(json.dumps(configure_sharing(
        profile, choice=choice, project_roots=opts.project_root,
        repository=opts.repository, fork=opts.fork, account=opts.account,
    ), sort_keys=True))
    return 0

def _doctor(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.api import run_host

    parser = argparse.ArgumentParser(prog="setup.py doctor")
    parser.add_argument("--probe-remote", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--timeout", type=float)
    opts = parser.parse_args(argv)
    command = ["--host", "kimi", "--plugin-root", str(PLUGIN_ROOT), "--config", str(profile.config_path), "doctor"]
    command += ["--probe-remote"] if opts.probe_remote else []
    command += ["--cwd", opts.cwd] if opts.cwd else []
    command += ["--timeout", str(opts.timeout)] if opts.timeout is not None else []
    return run_host(command)

def _update(profile: ProfileRef, argv: list[str]) -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime

    parser = argparse.ArgumentParser(prog="setup.py update")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--channel-base-url")
    parser.add_argument("--background", action="store_true")
    opts = parser.parse_args(argv)
    print(json.dumps(update_runtime(
        profile, force=opts.force, offline=opts.offline,
        channel_base_url=opts.channel_base_url, background=opts.background,
    ), sort_keys=True))
    return 0

def _declares(path: Path, parts: list[str]) -> bool:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    for part in parts:
        node = next((item for item in body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part), None)
        if node is None:
            return False
        body = node.body
    return True

def _target(raw: str) -> tuple[str, list[str]]:
    if "::" in raw:
        parts = raw.split("::")
        return parts[0], [part for part in parts[1:] if part]
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
    declaration = validate_compatibility(
        json.loads((source / "harness-compatibility.json").read_text(encoding="utf-8-sig")),
        harness_name=HARNESS,
    )
    manifest = json.loads((source / "kimi.plugin.json").read_text(encoding="utf-8-sig"))
    problems = [f"kimi.plugin.json version={manifest.get('version')!r}"] if manifest.get("version") != VERSION else []
    for entry in declaration["interface_registry"].values():
        targets = list(entry["code_paths"]) + (list(entry["test_ids"]) if opts.tests is not None else [])
        for raw in targets:
            relative, parts = _target(raw)
            path = source / relative
            if not path.is_file() or (path.suffix == ".py" and parts and not _declares(path, parts)):
                problems.append(raw)
    if problems:
        raise SystemExit("unresolved release metadata targets: " + "; ".join(sorted(set(problems))))
    print(json.dumps(validate_release_metadata({
        "schema": "mindie-plugin-release/1", "version": manifest["version"],
        "source": {"repository": REPOSITORY, "sha": opts.revision}, "compatibility": declaration,
    }, repository=REPOSITORY, harness_name=HARNESS), sort_keys=True))
    return 0

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args = list(raw_args)
    config: str | None = None
    while "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args):
            raise SystemExit("--config requires a path")
        config, args = args[index + 1], args[:index] + args[index + 2:]
    if _runtime_python() is None:
        import mindie_launch
        cfg_path = Path(config).expanduser().absolute() if config else mindie_launch._config_path()
        py = mindie_launch._active_python(cfg_path)
        if py is not None and py.resolve() != Path(sys.executable).resolve():
            return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=_NO_WIN).returncode
    command = args[0] if args and args[0] in COMMANDS else "init"
    rest = args[1:] if args and args[0] in COMMANDS else args
    if command == "release-metadata":
        return _release_metadata(rest)
    from mindie_harness.paths import ProfileRef
    profile = ProfileRef.resolve("kimi", plugin_root=PLUGIN_ROOT, config_path=config)
    if command in {"init", "install"}:
        return _init(profile, rest)
    return {"sharing": _sharing, "doctor": _doctor, "update": _update}[command](profile, rest)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
