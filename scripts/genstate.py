"""Generation registry and shared operation lock for update dispatch.

One committed generation = one immutable directory with its own pinned
venv. current.json is the atomic call-generation pointer; status.json is
the observable last-check record. The operation lock is shared by every
dispatched hook call and held exclusively only for the update switch.
Never writes ~/.kimi-code.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from paths import configured_python, plugin_root_from_env, state_dir

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


def read_current(config=None) -> dict:
    """Current committed generation. Falls back to the source tree itself."""
    data = read_json(current_path(config))
    if data is not None:
        generation = data.get("generation")
        python = data.get("python")
        if (
            isinstance(generation, str)
            and isinstance(python, str)
            and Path(generation).is_dir()
            and Path(python).exists()
        ):
            return {
                "generation": generation,
                "python": python,
                "sha": data.get("sha") if isinstance(data.get("sha"), str) else None,
            }
    try:
        python = configured_python(config)
    except Exception:
        python = sys.executable
    return {"generation": str(plugin_root_from_env()), "python": python, "sha": None}


def write_current(value: dict, config=None) -> None:
    atomic_write(current_path(config), value)


def read_status(config=None) -> dict:
    return read_json(status_path(config)) or {}


def write_status(value: dict, config=None) -> None:
    atomic_write(status_path(config), value)


class LockTimeout(RuntimeError):
    pass


class OperationLock:
    """Shared/exclusive file lock. Hooks take shared for their short run;

    MCP launchers take shared only while selecting a generation. The
    update switch takes exclusive with a bounded wait.
    """

    def __init__(self, config=None):
        self._path = lock_path(config)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _acquire(self, exclusive: bool, timeout: float):
        descriptor = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        deadline = time.monotonic() + timeout
        while True:
            try:
                if os.name == "nt":
                    # Windows byte-range lock; behaviour not natively verified.
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    mode = msvcrt.LK_NBLCK if exclusive else msvcrt.LK_NBLCK
                    msvcrt.locking(descriptor, mode, 1)
                else:
                    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
                    fcntl.flock(descriptor, flag | fcntl.LOCK_NB)
                return descriptor
            except OSError:
                if time.monotonic() >= deadline:
                    os.close(descriptor)
                    kind = "exclusive" if exclusive else "shared"
                    raise LockTimeout(f"timed out acquiring {kind} operation lock")
                time.sleep(0.1)

    @staticmethod
    def _release(descriptor) -> None:
        try:
            if os.name == "nt":
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)

    def shared(self, timeout=5.0):
        return _Held(self, False, timeout)

    def exclusive(self, timeout=30.0):
        return _Held(self, True, timeout)


class _Held:
    def __init__(self, lock, exclusive, timeout):
        self._lock = lock
        self._exclusive = exclusive
        self._timeout = timeout
        self._descriptor = None

    def __enter__(self):
        self._descriptor = self._lock._acquire(self._exclusive, self._timeout)
        return self

    def __exit__(self, *exc):
        if self._descriptor is not None:
            self._lock._release(self._descriptor)
            self._descriptor = None
        return False
