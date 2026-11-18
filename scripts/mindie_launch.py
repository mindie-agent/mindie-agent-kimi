#!/usr/bin/env python3
"""[Role] Pure-stdlib cold-start launcher: resolves the Kimi profile and forwards `mcp`/`hook`.
[Invariants] Zero third-party imports and zero SQLite on hook paths; `pretool` never reads stdin;
`stop` exits `{}` unless consent (`contribute`/`local-only`) and an absolute `project_roots` entry
authorize `cwd` via `os.path.commonpath`; `PYTHONPATH` is stripped only for a different venv.
[Timeouts & Locks] Bounded `1 MiB + 1` stdin; zero hook locks; seed provisioning owns its lock.
[Side-Effect Policy] Writes `state/kimi/pending-update-check.json`, provisions the verified seed
runtime when needed, then execs/spawns `entry.py`; stdout stays JSON-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_NO_WIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
SEED_SCHEMA = "mindie-harness-seed/1"
MAX_STOP_BYTES = 1048577

def _config_home() -> Path:
    if explicit := os.environ.get("MINDIE_CONFIG_HOME"):
        return Path(explicit).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") / "mindie-agent"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent"

def _data_home() -> Path:
    if explicit := os.environ.get("MINDIE_DATA_HOME"):
        return Path(explicit).expanduser()
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "mindie-agent"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "mindie-agent"

def _config_path() -> Path:
    raw = os.environ.get("MINDIE_KIMI_CONFIG") or os.environ.get("MINDIE_AGENT_CONFIG")
    return Path(raw).expanduser().absolute() if raw else (_config_home() / "kimi.json").absolute()

def _state_dir() -> Path:
    return _data_home() / "state" / "kimi"

def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig")) if path.is_file() else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

def _sharing_roots(cfg_path: Path) -> list[str]:
    consent, community_path = _read_json(cfg_path.parent / "mindie-consent.json") or {}, cfg_path.parent / "mindie-community.json"
    community, choice = _read_json(community_path), consent.get("choice")
    if choice not in {"contribute", "local-only"} and not (choice is None and consent.get("enabled") is True):
        return []
    if community_path.is_file():
        if not isinstance(community, dict) or community.get("enabled") is not True:
            return []
        roots = community.get("project_roots")
    else:
        roots = consent.get("project_roots")
    if not isinstance(roots, list) or not roots:
        return []
    if any(not isinstance(item, str) or not os.path.isabs(item) for item in roots):
        return []
    return [os.path.normcase(str(Path(item).resolve())) for item in roots]

def _in_roots(raw: bytes, roots: list[str]) -> bool:
    if not raw.strip() or len(raw) > MAX_STOP_BYTES - 1:
        return False
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return False
    if not isinstance(event, dict):
        return False
    if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]):
        items: list[object] = [event["cwd"]]
    else:
        extra = event.get("workspacePaths")
        items = [event.get(key) for key in ("cwd", "workspace", "project_root")] + (extra if isinstance(extra, list) else [])
    for item in items:
        if not isinstance(item, str) or not os.path.isabs(item):
            continue
        candidate = os.path.normcase(str(Path(item).resolve()))
        for root in roots:
            if os.path.splitdrive(candidate)[0] != os.path.splitdrive(root)[0]:
                continue
            try:
                if os.path.commonpath([candidate, root]) == root:
                    return True
            except (OSError, ValueError):
                continue
    return False

def _active_python(cfg_path: Path) -> Path | None:
    cfg = _read_json(cfg_path) or {}
    release = cfg.get("component_release")
    if isinstance(release, str) and release:
        receipt = _read_json(Path(release) / "runtime.json") or {}
        candidate = Path(str(receipt.get("python") or ""))
        if candidate.is_file() and (receipt.get("validation") or {}).get("status") == "validated":
            return candidate
    raw = cfg.get("python")
    return Path(raw) if isinstance(raw, str) and Path(raw).is_file() else None

def _has_harness() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("mindie_harness") is not None
    except (ImportError, ValueError):
        return False

def _verify_seed() -> None:
    assets = PLUGIN_ROOT / "assets"
    lock, pyz = _read_json(assets / "seed-lock.json") or {}, assets / "mindie-harness-seed.pyz"
    if lock.get("schema") != SEED_SCHEMA or not pyz.is_file():
        raise SystemExit("invalid bundled mindie-harness seed")
    digest, size = hashlib.sha256(), 0
    with pyz.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
    if size != lock.get("size_bytes") or digest.hexdigest() != lock.get("sha256"):
        raise SystemExit("mindie-harness-seed.pyz SHA-256 mismatch")

def _provision(cfg_path: Path) -> Path | None:
    _verify_seed()
    seed = PLUGIN_ROOT / "assets" / "mindie-harness-seed.pyz"
    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(seed), "--host", "kimi",
         "--plugin-root", str(PLUGIN_ROOT), "--config", str(cfg_path)],
        stdout=subprocess.DEVNULL, creationflags=_NO_WIN,
    )
    return _active_python(cfg_path) if result.returncode == 0 else None

def _enqueue(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = state_dir / f".pending-update-check.{os.getpid()}.tmp"
    try:
        payload = {"schema": "mindie-pending-update/1", "host": "kimi", "reason": "session_start", "requested_at": time.time()}
        tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, state_dir / "pending-update-check.json")
    finally:
        tmp.unlink(missing_ok=True)

def _dispatch(python: Path, cfg_path: Path, forwarded: list[str], stdin_buf: bytes | None, *, timeout: float | None = None) -> int:
    argv = [str(python), "-X", "utf8", str(HERE / "entry.py"), "--config", str(cfg_path), *forwarded]
    if Path(python).resolve() != Path(sys.executable).resolve():
        os.environ.pop("PYTHONPATH", None)
    if stdin_buf is None and os.name != "nt":
        os.execv(str(python), argv)
    return subprocess.run(argv, input=stdin_buf, capture_output=timeout is not None, timeout=timeout, creationflags=_NO_WIN).returncode

def _empty() -> int:
    sys.stdout.write("{}\n")
    sys.stdout.flush()
    return 0

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cfg_arg: str | None = None
    while "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args):
            raise SystemExit("--config requires a path")
        cfg_arg, args = args[index + 1], args[:index] + args[index + 2:]
    cfg_path = Path(cfg_arg).expanduser().absolute() if cfg_arg else _config_path()
    if len(args) < 2:
        raise SystemExit("usage: mindie_launch.py [--config PATH] mcp <surface> | hook <event>")
    family, operation = args[0], args[1].lower().replace("_", "-")
    if family == "mcp" and operation in {"knowledge", "remote", "components", "tools", "all"}:
        python = _active_python(cfg_path) or (Path(sys.executable) if _has_harness() else None) or _provision(cfg_path)
        return 1 if python is None else _dispatch(python, cfg_path, ["mcp", operation], None)
    if family == "hook" and operation in {"pretool", "session-start"}:
        if operation == "session-start":
            try:
                _enqueue(_state_dir())
            except TimeoutError:
                raise
            except OSError:
                pass
        return _empty()
    if family == "hook" and operation == "stop":
        roots = _sharing_roots(cfg_path)
        if not roots:
            return _empty()
        stdin_buf = sys.stdin.buffer.read(MAX_STOP_BYTES)
        python = _active_python(cfg_path) if _in_roots(stdin_buf, roots) else None
        if python is not None:
            try:
                _dispatch(python, cfg_path, ["hook", "stop"], stdin_buf, timeout=8.0)
            except subprocess.TimeoutExpired:
                pass
        return _empty()
    raise SystemExit(f"unsupported operation: {family} {operation}")

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
