#!/usr/bin/env python3
"""Stable MindIE launcher living outside any committed generation.

Installed Kimi manifest entries point absolute command/args here.

current.json is the atomic generation tuple
{generation, python, adapter_config, sha}. Every tools/list and tools/call
selects under the shared operation lock and holds it for the complete child.
The child gets MINDIE_KIMI_CONFIG=current.adapter_config and no PYTHONPATH.

- hook <op>: Sharing-off / unconfigured Stop returns {} before waiting for
  stdin and before any update.lock, state, help, or service. Otherwise read
  bounded stdin under the remaining whole-hook budget (never block until EOF
  past the deadline), forward it exactly. Whole budget is under the native 2s
  including lock, stdin, child, and cleanup. Failures print {} and exit 0;
  never exit 2 (continuation). No automatic retries.
- mcp <surface>: long-lived thin stdlib front. initialize/ping locally;
  every tools/list and tools/call is one bounded --once dispatch. Never replay.
- updater <op>: read the current tuple without the shared lock (updater owns
  its locks) and exec that generation's updater.py.

Windows uses a real msvcrt byte lock; if no real lock can be taken the call
fails closed — unprotected dispatch is never executed. Windows process-tree
protection lives in bounded.py (taskkill /T) and is not natively verified.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import bounded

MAX_LINE = 128 * 1024
MAX_HOOK_BYTES = 128 * 1024
HOOK_TOTAL = 1.8
HOOK_LOCK_BUDGET = 0.3
CALL_LOCK_BUDGET = 5.0
CALL_CHILD_BUDGET = 60.0


def _config_path() -> Path:
    override = os.environ.get("MINDIE_KIMI_CONFIG")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mindie-agent" / "kimi.json"


def _load_config():
    try:
        data = json.loads(_config_path().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _state_dir(config=None) -> Path:
    if config is None:
        config = _load_config()
    if isinstance(config, dict):
        value = config.get("state_dir")
        if isinstance(value, str) and os.path.isabs(value):
            return Path(value)
        engine = config.get("engine_config")
        if isinstance(engine, str):
            try:
                root = json.loads(Path(engine).read_text()).get("root")
                if isinstance(root, str):
                    return Path(root) / "kimi-adapter"
            except (OSError, ValueError):
                pass
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "mindie-agent" / "state"


def _sharing_enabled() -> bool:
    """True only when community sharing is explicitly enabled.

    Missing config or any read failure is off. Reads existing files only;
    never creates update.lock, state, or a service.
    """
    config = _load_config()
    if not isinstance(config, dict):
        return False
    community = config.get("community_config")
    if not isinstance(community, str) or not os.path.isabs(community):
        return False
    try:
        data = json.loads(Path(community).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("enabled") is True


class LockUnavailable(RuntimeError):
    pass


def _try_lock_shared(descriptor) -> None:
    if os.name == "nt":
        import msvcrt

        # Real Windows byte lock (not verified on real hardware). LK_NBLCK is
        # exclusive; that is fail-closed relative to POSIX LOCK_SH.
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)


def _unlock(descriptor) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass


def _shared_lock(state: Path, deadline: float):
    """Bounded nonblocking shared lock. Never executes unprotected."""
    lock_path = state / "update" / "operation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        while True:
            try:
                _try_lock_shared(descriptor)
                acquired = True
                return descriptor
            except OSError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LockUnavailable("shared operation lock unavailable")
                time.sleep(min(0.05, remaining))
    finally:
        if not acquired:
            os.close(descriptor)


def _release(descriptor) -> None:
    _unlock(descriptor)
    os.close(descriptor)


def _current(state: Path) -> dict:
    try:
        data = json.loads((state / "update" / "current.json").read_text())
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        generation = data.get("generation")
        python = data.get("python")
        adapter_config = data.get("adapter_config")
        sha = data.get("sha")
        if (
            isinstance(generation, str)
            and isinstance(python, str)
            and isinstance(adapter_config, str)
            and Path(generation).is_dir()
            and Path(python).exists()
            and Path(adapter_config).is_file()
        ):
            return {
                "generation": generation,
                "python": python,
                "adapter_config": adapter_config,
                "sha": sha if isinstance(sha, str) else None,
            }
    raise RuntimeError("no committed generation is available")


def _child_env(current: dict) -> dict:
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    env["MINDIE_KIMI_CONFIG"] = current["adapter_config"]
    return env


def _fail_open() -> int:
    print(json.dumps({}), flush=True)
    return 0


def _read_hook_stdin(deadline: float) -> bytes:
    """Read at most MAX_HOOK_BYTES before deadline. Never block until EOF.

    A writer that sends one byte and keeps the pipe open must not exceed the
    remaining whole-hook budget. Oversized or timed-out input is empty (fail
    open). Memory is capped at MAX_HOOK_BYTES+1. Windows uses the same thread
    reader (code-only; not natively verified).
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return b""
    buf = bytearray()
    lock = threading.Lock()
    finished = threading.Event()

    def reader():
        try:
            fd = sys.stdin.fileno()
            while True:
                with lock:
                    if len(buf) > MAX_HOOK_BYTES:
                        return
                    room = MAX_HOOK_BYTES + 1 - len(buf)
                try:
                    chunk = os.read(fd, min(8192, room))
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                with lock:
                    buf.extend(chunk)
                    if len(buf) > MAX_HOOK_BYTES:
                        return
        finally:
            finished.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    finished.wait(timeout=max(0.0, deadline - time.monotonic()))
    if not finished.is_set():
        return b""
    with lock:
        raw = bytes(buf)
    if len(raw) > MAX_HOOK_BYTES:
        return b""
    return raw


def _hook(op: str) -> int:
    started = time.monotonic()
    deadline = started + HOOK_TOTAL
    if op == "stop" and not _sharing_enabled():
        return _fail_open()
    raw = _read_hook_stdin(deadline)
    lock_deadline = min(deadline, time.monotonic() + HOOK_LOCK_BUDGET)
    if lock_deadline <= time.monotonic():
        return _fail_open()
    try:
        state = _state_dir()
        descriptor = _shared_lock(state, lock_deadline)
    except Exception:
        return _fail_open()
    try:
        current = _current(state)
        script = Path(current["generation"]) / "scripts" / "bridge.py"
        if not script.is_file():
            return _fail_open()
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            return _fail_open()
        try:
            out = bounded.run(
                [current["python"], str(script), op],
                raw,
                timeout=remaining,
                env=_child_env(current),
                cwd=str(Path(current["generation"])),
                check=False,
            )
        except Exception:
            return _fail_open()
        text = out.strip() or "{}"
        try:
            value = json.loads(text.splitlines()[0] if text else "{}")
        except ValueError:
            return _fail_open()
        if not isinstance(value, dict):
            return _fail_open()
        print(json.dumps(value, ensure_ascii=False), flush=True)
        return 0
    except Exception:
        return _fail_open()
    finally:
        _release(descriptor)


def _local_answer(message: dict):
    method = message.get("method")
    ident = message.get("id")
    if method == "initialize":
        return dict(
            jsonrpc="2.0",
            id=ident,
            result=dict(
                protocolVersion="2025-11-25",
                capabilities={"tools": {}},
                serverInfo={"name": "mindie-kimi-front", "version": "0.1.0"},
            ),
        )
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    return None


def _error(ident, text: str):
    return dict(
        jsonrpc="2.0",
        id=ident,
        result=dict(
            content=[
                dict(
                    type="text",
                    text=f"Unavailable: {text}. Continue independently."[:500],
                )
            ],
            isError=True,
        ),
    )


def _dispatch(surface: str, raw: bytes, ident):
    """One bounded one-shot request through CURRENT generation. Never replayed."""
    state = _state_dir()
    deadline = time.monotonic() + CALL_LOCK_BUDGET
    descriptor = _shared_lock(state, deadline)
    try:
        current = _current(state)
        script = Path(current["generation"]) / "scripts" / "mcp_server.py"
        if not script.is_file():
            raise RuntimeError("committed generation lacks mcp_server.py")
        payload = raw if raw.endswith(b"\n") else raw + b"\n"
        out = bounded.run(
            [current["python"], str(script), surface, "--once"],
            payload,
            timeout=CALL_CHILD_BUDGET,
            env=_child_env(current),
            cwd=str(Path(current["generation"])),
        )
        lines = [line for line in out.splitlines() if line.strip()]
        if len(lines) != 1:
            raise RuntimeError("generation returned no single response; not replayed")
        response = json.loads(lines[0])
        if not isinstance(response, dict):
            raise RuntimeError("generation returned an invalid response; not replayed")
        if response.get("id") != ident:
            raise RuntimeError("generation response id does not match request; not replayed")
        return response
    finally:
        _release(descriptor)


def _read_mcp_line(stdin, limit: int):
    """Return bytes, None to skip an oversize line, or False on EOF."""
    line = stdin.readline(limit + 1)
    if line == b"":
        return False
    if len(line) > limit and not line.endswith(b"\n"):
        while True:
            chunk = stdin.readline(limit + 1)
            if not chunk or chunk.endswith(b"\n"):
                break
        return None
    if line.endswith(b"\n"):
        line = line[:-1]
    if len(line) > limit:
        return None
    return line


def _mcp(surface: str) -> int:
    write = sys.stdout.buffer
    stdin = sys.stdin.buffer
    while True:
        raw = _read_mcp_line(stdin, MAX_LINE)
        if raw is False:
            break
        if raw is None or not raw.strip():
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(message, dict) or "id" not in message:
            continue
        ident = message["id"]
        response = _local_answer(message)
        if response is None:
            if message.get("method") not in {"tools/list", "tools/call"}:
                response = dict(
                    jsonrpc="2.0",
                    id=ident,
                    error=dict(code=-32601, message="Method not found"),
                )
            else:
                try:
                    response = _dispatch(surface, raw, ident)
                except Exception as exc:
                    response = _error(ident, str(exc)[:300])
        write.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
        write.flush()
    return 0


def _updater(rest) -> int:
    """Select the current tuple and exec that generation's updater.

    No shared operation lock is held across the updater run.
    """
    try:
        current = _current(_state_dir())
    except Exception as exc:
        print(f"no committed generation: {exc}"[:400], file=sys.stderr)
        return 2
    script = Path(current["generation"]) / "scripts" / "updater.py"
    if not script.is_file():
        print("committed generation lacks updater.py", file=sys.stderr)
        return 2
    os.execve(
        current["python"],
        [current["python"], str(script), *rest],
        _child_env(current),
    )
    return 2


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kind = argv[0] if argv else ""
    name = argv[1] if len(argv) > 1 else ""
    if kind == "hook" and name in {"pretool", "stop"}:
        return _hook(name)
    if kind == "mcp" and name in {"knowledge", "remote"}:
        return _mcp(name)
    if kind == "updater":
        return _updater(argv[1:])
    if kind == "hook":
        return _fail_open()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
