#!/usr/bin/env python3
"""Stable MindIE launcher living outside any committed generation.

Installed Kimi manifest entries point absolute command/args here. This
launcher resolves the CURRENT committed generation through the atomic
current.json pointer and dispatches one call into exactly that
generation's own interpreter and scripts (never a mix).

- hook <op>: short-lived; holds the shared operation lock for the whole
  child run so the update switch never swaps configs mid-hook.
- mcp <surface>: long-lived front; selects its generation under the
  shared lock, then execs that generation's MCP server. In-flight calls
  are protected by core stop_if_idle during the switch; an old loaded
  server stays callable because its generation directory is kept.

Stdlib only. Hook failures print {} and exit 0 (hooks fail open).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

LOCK_TIMEOUT = 5.0


def _config_path() -> Path:
    override = os.environ.get("MINDIE_KIMI_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mindie-agent" / "kimi.json"


def _state_dir(config: dict | None) -> Path:
    if config:
        value = config.get("state_dir")
        if isinstance(value, str) and os.path.isabs(value):
            return Path(value)
        engine = config.get("engine_config")
        if isinstance(engine, str):
            try:
                data = json.loads(Path(engine).read_text())
                root = data.get("root")
                if isinstance(root, str):
                    return Path(root) / "kimi-adapter"
            except (OSError, ValueError):
                pass
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mindie-agent" / "state"


def _load_config() -> dict | None:
    try:
        data = json.loads(_config_path().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _current(state: Path) -> dict:
    try:
        data = json.loads((state / "update" / "current.json").read_text())
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        generation = data.get("generation")
        python = data.get("python")
        if (
            isinstance(generation, str)
            and isinstance(python, str)
            and Path(generation).is_dir()
            and Path(python).exists()
        ):
            return {"generation": generation, "python": python}
    raise RuntimeError("no committed generation is available")


def _shared_lock(state: Path):
    if os.name == "nt":
        return None  # Windows lock parity is unverified; dispatch still works.
    import fcntl

    state.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(state / "update" / "operation.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    return descriptor


def _fail_open() -> int:
    print(json.dumps({}), flush=True)
    return 0


def _script(generation: Path, kind: str) -> Path:
    return generation / "scripts" / ("mcp_server.py" if kind == "mcp" else "bridge.py")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kind = argv[0] if argv else ""
    name = argv[1] if len(argv) > 1 else ""
    is_hook = kind == "hook"
    if (kind, name) not in {
        ("mcp", "knowledge"),
        ("mcp", "remote"),
        ("hook", "pretool"),
        ("hook", "stop"),
    }:
        return _fail_open() if is_hook or kind not in {"mcp", "hook"} else 2
    try:
        config = _load_config()
        state = _state_dir(config)
        lock_fd = _shared_lock(state)
        try:
            current = _current(state)
        finally:
            if lock_fd is not None and kind == "mcp":
                # Selection is done under the shared lock; the long-lived
                # server must not hold it. Hooks keep it until exit below.
                import fcntl

                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
                lock_fd = None
        generation = Path(current["generation"])
        script = _script(generation, kind)
        if not script.is_file():
            raise RuntimeError(f"generation lacks scripts: {generation}")
        # Dispatch with the generation's OWN pinned interpreter. Never
        # route through with_runtime here: it re-reads the live adapter
        # config and could mix old scripts with a newer interpreter.
        command = [current["python"], str(script), name]
        if kind == "mcp":
            os.execv(command[0], command)
            return 2  # unreachable
        try:
            completed = subprocess.run(command, timeout=10)
            return completed.returncode
        except subprocess.TimeoutExpired:
            return _fail_open()
        finally:
            if lock_fd is not None:
                os.close(lock_fd)
    except Exception:
        return _fail_open() if is_hook else 2


if __name__ == "__main__":
    raise SystemExit(main())
