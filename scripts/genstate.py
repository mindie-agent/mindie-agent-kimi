"""Generation registry and operation locks for update dispatch.

current.json is ONE atomic committed tuple:
  {generation: absolute code root, python: pinned interpreter,
   adapter_config: absolute generation-specific adapter JSON, sha: exact commit}
The launcher sets MINDIE_KIMI_CONFIG=current.adapter_config per child, so
code, interpreter, and config always come from the same committed
generation. Base adapter config retains the stable state_dir.

Locks: the per-call shared operation lock (hooks/MCP dispatch/feed sync),
the exclusive switch lock (updater), and a nonblocking check.lock
serializing scheduled checks. Never writes ~/.kimi-code.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from paths import config_path, configured_python, plugin_root_from_env, state_dir

if os.name == "nt":
    import msvcrt
else:
    import fcntl


def update_dir(config=None) -> Path:
    return state_dir(config) / "update"


def generations_dir(config=None) -> Path:
    return update_dir(config) / "generations"


def current_path(config=None) -> Path:
    return update_dir(config) / "current.json"


def status_path(config=None) -> Path:
    return update_dir(config) / "status.json"


def failed_path(config=None) -> Path:
    return update_dir(config) / "failed.json"


def receipts_dir(config=None) -> Path:
    return update_dir(config) / "receipts"


def lock_path(config=None) -> Path:
    return update_dir(config) / "operation.lock"


def check_lock_path(config=None) -> Path:
    return update_dir(config) / "check.lock"


def launch_dir(config=None) -> Path:
    return update_dir(config) / "launch"


def atomic_write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    descriptor = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    os.replace(tmp, path)


def read_json(path: Path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _valid_tuple(data) -> dict | None:
    if not isinstance(data, dict):
        return None
    generation = data.get("generation")
    python = data.get("python")
    adapter_config = data.get("adapter_config")
    if not (
        isinstance(generation, str)
        and isinstance(python, str)
        and isinstance(adapter_config, str)
        and Path(generation).is_dir()
        and Path(python).exists()
        and Path(adapter_config).is_file()
    ):
        return None
    return {
        "generation": generation,
        "python": python,
        "adapter_config": adapter_config,
        "sha": data.get("sha") if isinstance(data.get("sha"), str) else None,
    }


def read_current(config=None) -> dict:
    """Current committed generation tuple. Bootstrap: the source tree with
    the base adapter config."""
    data = _valid_tuple(read_json(current_path(config)))
    if data is not None:
        return data
    try:
        python = configured_python(config)
    except Exception:
        python = sys.executable
    return {
        "generation": str(plugin_root_from_env()),
        "python": python,
        "adapter_config": str(config_path()),
        "sha": None,
    }


def write_current(value: dict, config=None) -> None:
    if _valid_tuple(value) is None:
        raise ValueError("refusing to commit an invalid generation tuple")
    atomic_write(current_path(config), value)


def read_status(config=None) -> dict:
    return read_json(status_path(config)) or {}


def write_status(value: dict, config=None) -> None:
    atomic_write(status_path(config), value)


class LockTimeout(RuntimeError):
    pass


def _lock_once(descriptor, exclusive: bool) -> bool:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(descriptor, flag | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(descriptor) -> None:
    try:
        if os.name == "nt":
            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    os.close(descriptor)


class _Held:
    def __init__(self, path: Path, exclusive: bool, timeout: float):
        self._path = path
        self._exclusive = exclusive
        self._timeout = timeout
        self._descriptor = None

    def __enter__(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + self._timeout
        while not _lock_once(descriptor, self._exclusive):
            if time.monotonic() >= deadline:
                os.close(descriptor)
                kind = "exclusive" if self._exclusive else "shared"
                raise LockTimeout(f"timed out acquiring {kind} operation lock")
            time.sleep(0.05)
        self._descriptor = descriptor
        return self

    def __exit__(self, *exc):
        if self._descriptor is not None:
            _unlock(self._descriptor)
            self._descriptor = None
        return False


class OperationLock:
    """Per-call shared lock / exclusive switch lock with bounded wait."""

    def __init__(self, config=None):
        self._path = lock_path(config)

    def shared(self, timeout=5.0):
        return _Held(self._path, False, timeout)

    def exclusive(self, timeout=30.0):
        return _Held(self._path, True, timeout)


def check_lock(config=None):
    """Nonblocking whole-check serialization: raises LockTimeout if another
    check is running."""
    config_path_ = check_lock_path(config)
    return _Held(config_path_, True, 0.0)
