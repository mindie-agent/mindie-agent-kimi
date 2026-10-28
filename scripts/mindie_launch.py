#!/usr/bin/env python3
"""Stable MindIE launcher outside committed generations for Kimi Hook/MCP/Updater.

[Role]
Serves as the stable command entrypoint referenced by installed Kimi plugin
manifests (`hook <pretool|stop>`, `mcp <knowledge|remote|components>`, `updater <op>`).
Selects the active committed generation from `<state_dir>/update/current.json`
under the shared `operation.lock` and dispatches one-shot children.

[Invariants]
- `current.json` holds the atomic generation tuple `{generation, python,
  adapter_config, sha}`.
- Unconfigured or sharing-off `stop` hooks return `{}` immediately before reading
  stdin or taking locks.
- `mcp` handles `initialize`/`ping` locally and dispatches each `tools/list` and
  `tools/call` as a single `--once` subprocess under the shared operation lock.

[Timeouts & Locks]
- `HOOK_TOTAL = 10.0s`, `HOOK_LOCK_BUDGET = 2.0s`, `BOOTSTRAP_TOTAL = 540.0s`,
  `CALL_LOCK_BUDGET = 5.0s`, `CALL_CHILD_BUDGET = 60.0s` (180s for explicit entry activation/configuration including reporter startup; remote calls derive native wait + 60s grace: Bash foreground/Read/Glob/Grep 360s, background launch 180s), `EOF_JOIN = 3.0s`,
  `OUTPUT_BUDGET = 1.0s`.
- Components add 30s to their requested deadline; feed sync reserves 150s.
- Acquires shared `operation.lock` (`fcntl.flock` / `msvcrt.locking`) before
  reading `current.json` and holds it across child execution.

[Side-Effect Policy]
- Spawns `bridge.py` (`hook`), `mcp_server.py --once` (`mcp`), or execs
  `updater.py` (`updater`) in the committed generation's interpreter.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import time
from pathlib import Path


def _load_diagnostic_support():
    directory = Path(__file__).resolve().parent

    def load(name):
        path = directory / (name + ".py")
        if not path.is_file() and (
            directory.parent.name == "launch"
            and directory.parent.parent.name == "update"
            and (directory.name == "bootstrap" or re.fullmatch(r"[0-9a-f]{40}", directory.name))
        ):
            # Immutable older builders copy only their known support files.
            # Resolve a new helper from this launcher's own generation.
            path = directory.parent.parent / "generations" / directory.name / "scripts" / (name + ".py")
        if not path.is_file():
            raise ModuleNotFoundError("MindIE generation diagnostics are missing")
        existing = sys.modules.get(name)
        if existing is not None and getattr(existing, "__file__", None) == str(path):
            return existing
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            sys.modules.pop(name, None)
            raise
        return module

    load("paths")
    load("launch_budget")
    return load("diagnostic_support")


import bounded

diagnostic_support = _load_diagnostic_support()

HOOK_TOTAL = float(os.environ.get("MINDIE_HOOK_TOTAL_SECONDS") or 10.0)
HOOK_LOCK_BUDGET = float(os.environ.get("MINDIE_HOOK_LOCK_BUDGET_SECONDS") or 2.0)
BOOTSTRAP_TOTAL = float(os.environ.get("MINDIE_BOOTSTRAP_TOTAL_SECONDS") or 540.0)
CALL_LOCK_BUDGET = 5.0
CALL_CHILD_BUDGET = 60.0
KNOWLEDGE_MAX_OUTPUT = 1024 * 1024
REMOTE_MAX_OUTPUT = bounded.REMOTE_MAX_OUTPUT
from launch_budget import (
    REMOTE_GRACE_SECONDS, REMOTE_MIN_BUDGET_SECONDS,
    kimi_remote_inner_seconds, remote_budget,
)
KIMI_REMOTE_PROFILE = "kimi"
MAX_RPC_ID = 256
WORK_LIMIT = 3
CONTROL_LIMIT = 1
EOF_JOIN = 3.0
OUTPUT_BUDGET = 1.0
CONTROL_TOOLS = {"remote_job_status", "remote_job_stop", "remote_job_tail"}
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")

_CONFIG_FILE = None


def _config_path() -> Path:
    if _CONFIG_FILE:
        return Path(_CONFIG_FILE).expanduser()
    if override := os.environ.get("MINDIE_KIMI_CONFIG"):
        return Path(override).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "mindie-agent" / "kimi.json"


def _load_config():
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except TimeoutError:
        raise
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
                root = json.loads(Path(engine).read_text(encoding="utf-8")).get("root")
                if isinstance(root, str):
                    return Path(root) / "kimi-adapter"
            except TimeoutError:
                raise
            except (OSError, ValueError):
                pass
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / "mindie-agent" / "state"


def _sharing_enabled() -> bool:
    path = _config_path()
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        _record_hook("sharing-probe", "configuration")
        return False
    if not isinstance(data, dict):
        _record_hook("sharing-probe", "configuration")
        return False
    community = data.get("community_config")
    if not isinstance(community, str) or not os.path.isabs(community):
        return False
    community_path = Path(community)
    if not community_path.is_file():
        return False
    try:
        raw = json.loads(community_path.read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        _record_hook("sharing-probe", "configuration")
        return False
    return isinstance(raw, dict) and raw.get("enabled") is True


class LockUnavailable(RuntimeError):
    pass


class DispatchFailure(RuntimeError):
    def __init__(self, diagnostic, stage):
        super().__init__(stage)
        self.diagnostic = diagnostic
        self.stage = stage


def _dispatch_failure(current, stage, category, exc, started):
    elapsed_ms = max(0, int((time.monotonic() - started) * 1000))
    diagnostic = diagnostic_support.failure(
        "mcp_dispatch", stage, category, exception=exc, elapsed_ms=elapsed_ms
    )
    return DispatchFailure(diagnostic, stage)


def _try_lock_shared(descriptor) -> None:
    if os.name == "nt":
        import msvcrt

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
    except TimeoutError:
        raise
    except OSError:
        pass


def _shared_lock(state: Path, deadline: float, cancel=None):
    lock_path = state / "update" / "operation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    acquired = False
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise bounded.CommandCancelled("command cancelled")
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
        data = json.loads((state / "update" / "current.json").read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        data = None
    if isinstance(data, dict):
        generation = data.get("generation")
        python = data.get("python")
        adapter_config = data.get("adapter_config")
        sha = data.get("sha")
        if (
            isinstance(generation, str) and Path(generation).is_dir()
            and isinstance(python, str) and Path(python).exists()
            and isinstance(adapter_config, str) and Path(adapter_config).is_file()
        ):
            return {
                "generation": generation,
                "python": python,
                "adapter_config": adapter_config,
                "sha": sha if isinstance(sha, str) else None,
            }
    raise RuntimeError("no committed generation is available")


def _child_env(current: dict, surface: str | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONIOENCODING"] = "utf-8"
    env["MINDIE_KIMI_CONFIG"] = current["adapter_config"]
    env["MINDIE_DIAGNOSTICS_CONFIG"] = str(diagnostic_support.reporting_config_path())
    if surface == "remote":
        # The installed manifest can predate the env field (0.1.2/0.1.3
        # packages rebuilt by the old updater dropped it). Bind the trusted
        # profile here, before the child imports remote_dev for tools/list.
        env["REMOTE_DEV_HARNESS_PROFILE"] = KIMI_REMOTE_PROFILE
    return env


def _fail_open() -> int:
    print(json.dumps({}), flush=True)
    return 0


def _record_hook(stage, category, op="stop"):
    try:
        diagnostic_support.failure("hook", stage, category, reportable=(op != "stop"))
    except TimeoutError:
        raise
    except Exception:
        pass


def _read_hook_stdin(deadline):
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
                try:
                    chunk = os.read(fd, 65536)
                except TimeoutError:
                    raise
                except (OSError, ValueError):
                    return
                if not chunk:
                    return
                with lock:
                    buf.extend(chunk)
                    if not chunk.rstrip().endswith((b"}", b"]")):
                        continue
                    try:
                        json.loads(bytes(buf))
                    except ValueError:
                        continue
                    return
        finally:
            finished.set()

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    finished.wait(timeout=max(0.0, remaining))
    if not finished.is_set():
        return b""
    with lock:
        return bytes(buf)


def _hook(op: str) -> int:
    started = time.monotonic()
    deadline = started + HOOK_TOTAL
    if op == "stop" and not _sharing_enabled():
        return _fail_open()
    if not _config_path().is_file():
        return _fail_open()
    config = _load_config()
    if config is None:
        _record_hook("entry", "configuration", op)
        return _fail_open()
    raw = _read_hook_stdin(deadline)
    lock_deadline = min(deadline, time.monotonic() + HOOK_LOCK_BUDGET)
    if lock_deadline <= time.monotonic():
        _record_hook("entry", "deadline", op)
        return _fail_open()
    try:
        state = _state_dir(config)
        descriptor = _shared_lock(state, lock_deadline)
    except TimeoutError:
        raise
    except Exception:
        _record_hook("entry", "lock-unavailable", op)
        return _fail_open()
    try:
        try:
            current = _current(state)
        except TimeoutError:
            raise
        except Exception:
            _record_hook("entry", "generation-unavailable", op)
            return _fail_open()
        script = Path(current["generation"]) / "scripts" / "bridge.py"
        if not script.is_file():
            diagnostic_support.failure("hook", "helper_missing", "missing_committed_file", reportable=(op != "stop"))
            return _fail_open()
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            return _fail_open()
        started = time.monotonic()

        def _hook_failure(stage, category, exc=None):
            diagnostic_support.failure(
                "hook", stage, category, exception=exc,
                reportable=(op != "stop"),
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
            )

        try:
            out = bounded.run(
                [current["python"], str(script), op],
                raw, timeout=remaining, env=_child_env(current),
                cwd=str(Path(current["generation"])), check=True,
            )
        except bounded.CommandTimedOut as exc:
            _hook_failure("helper_deadline", "child_deadline", exc)
            return _fail_open()
        except TimeoutError:
            raise
        except Exception as exc:
            _hook_failure("helper_failed", "child_failure", exc)
            return _fail_open()
        text = out.strip() or "{}"
        try:
            value = json.loads(text.splitlines()[0] if text else "{}")
        except ValueError:
            _hook_failure("helper_protocol", "protocol_mismatch")
            return _fail_open()
        if not isinstance(value, dict):
            _hook_failure("helper_protocol", "protocol_mismatch")
            return _fail_open()
        print(json.dumps(value, ensure_ascii=False), flush=True)
        return 0
    except TimeoutError:
        raise
    except Exception:
        return _fail_open()
    finally:
        _release(descriptor)


def _local_answer(message: dict):
    method = message.get("method")
    ident = message.get("id")
    if method == "initialize":
        return dict(
            jsonrpc="2.0", id=ident,
            result=dict(
                protocolVersion="2025-11-25",
                capabilities={"tools": {}},
                serverInfo={"name": "mindie-kimi-front", "version": diagnostic_support.plugin_version()},
            ),
        )
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    return None


def _valid_rpc_id(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    return isinstance(value, int) or (isinstance(value, str) and 1 <= len(value) <= MAX_RPC_ID)


def _local_tool_name(message):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    name = params.get("name")
    return name.rsplit("__", 1)[-1] if isinstance(name, str) and name else None


def _validated_remote_ref(message):
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    for key in ("job_id", "session_id"):
        value = args.get(key)
        if isinstance(value, str) and JOB_ID_RE.fullmatch(value):
            return key, value
    return None, None


def _front_reject(ident, text: str):
    return dict(
        jsonrpc="2.0", id=ident,
        result=dict(content=[dict(type="text", text=text[:500])], isError=True),
    )


def _stage_error(ident, message, stage: str, surface: str):
    structured = {"stage": stage}
    text = f"Unavailable ({stage}). Continue independently."
    if surface == "remote":
        structured["remote_outcome"] = "unconfirmed"
        key, value = _validated_remote_ref(message)
        name = _local_tool_name(message)
        if key:
            structured[key] = value
            text = f"Remote outcome unconfirmed ({stage}). Inspect or stop original {key} {value} before a deliberate repeat."
        elif name == "remote_bash":
            text = f"Remote submission unconfirmed ({stage}). Inspect local remote-dev job records before a deliberate repeat."
    return dict(
        jsonrpc="2.0", id=ident,
        result=dict(content=[dict(type="text", text=text[:500])], isError=True, structuredContent=structured),
    )


def _call_budget(surface, raw):
    message = json.loads(raw)
    arguments = (message.get("params") or {}).get("arguments") or {}
    name = _local_tool_name(message)
    if surface == "remote":
        return remote_budget(name, arguments)
    if name == "knowledge_sync":
        # Native nonce claim/finish and target-domain admission each allow 5s;
        # the 120s sync child also needs process cleanup and response encoding.
        return 150.0
    if name == "mindie_entry" and arguments.get("op") in {
        "init", "choose", "sharing-enable", "sharing-disable",
    }:
        return 180.0
    if _local_tool_name(message) in {
        "profiler", "sim", "coordinator", "diagnostics_status", "diagnostics_bundle",
    }:
        milliseconds = arguments.get("timeout_ms", 120000)
        if type(milliseconds) is int and 1 <= milliseconds <= 600000:
            # Nonce claim/finish, a failed-receipt diagnostic write, and tree
            # cleanup must fit after the component's execution deadline.
            return milliseconds / 1000 + 30.0
    return CALL_CHILD_BUDGET


def _response_budget(surface, raw):
    if surface == "components":
        arguments = (json.loads(raw).get("params") or {}).get("arguments") or {}
        tokens = arguments.get("max_output_tokens", 16384)
        if type(tokens) is int and 1 <= tokens <= 262144:
            # Each stream appears in structuredContent and serialized text;
            # worst-case JSON escaping expands one byte into six characters.
            return tokens * 4 * 16 + 65536
    return KNOWLEDGE_MAX_OUTPUT if surface == "knowledge" else REMOTE_MAX_OUTPUT


def _dispatch(surface: str, raw: bytes, ident, cancel=None):
    if cancel is not None and cancel.is_set():
        raise bounded.CommandCancelled("command cancelled")
    state = _state_dir()
    descriptor = _shared_lock(state, time.monotonic() + CALL_LOCK_BUDGET, cancel=cancel)
    try:
        if cancel is not None and cancel.is_set():
            raise bounded.CommandCancelled("command cancelled")
        current = _current(state)
        started = time.monotonic()
        script = Path(current["generation"]) / "scripts" / "mcp_server.py"
        if not script.is_file():
            raise _dispatch_failure(current, "helper_missing", "missing_committed_file", None, started)
        payload = raw if raw.endswith(b"\n") else raw + b"\n"
        try:
            out = bounded.run(
                [current["python"], str(script), surface, "--once"],
                payload, timeout=_call_budget(surface, raw), env=_child_env(current, surface),
                cwd=str(Path(current["generation"])), cancel=cancel,
                max_output=_response_budget(surface, raw),
            )
        except bounded.CommandCancelled:
            raise
        except bounded.CommandTimedOut as exc:
            raise _dispatch_failure(current, "helper_deadline", "child_deadline", exc, started) from None
        except Exception as exc:
            raise _dispatch_failure(current, "helper_failed", "child_failure", exc, started) from None
        if cancel is not None and cancel.is_set():
            raise bounded.CommandCancelled("command cancelled")
        lines = [line for line in out.splitlines() if line.strip()]
        if len(lines) != 1:
            raise _dispatch_failure(current, "helper_protocol", "protocol_mismatch", None, started)
        try:
            response = json.loads(lines[0])
        except ValueError:
            raise _dispatch_failure(current, "helper_protocol", "protocol_mismatch", None, started) from None
        if not isinstance(response, dict) or response.get("id") != ident:
            raise _dispatch_failure(current, "helper_protocol", "protocol_mismatch", None, started)
        return response
    finally:
        _release(descriptor)


def _read_mcp_line(stdin):
    line = stdin.readline()
    return line.rstrip(b"\r\n") if line else False


def _mcp(surface: str) -> int:
    output_fd = sys.stdout.fileno()
    # MCP stdout is a pipe; Python 3.12+ supports nonblocking Windows pipes.
    os.set_blocking(output_fd, False)
    stdin = sys.stdin.buffer
    output_lock = threading.Lock()
    transport_closed = threading.Event()
    pending_lock = threading.Lock()
    pending = {}
    workers = []
    active_work = 0
    active_control = 0

    def send(response):
        deadline = time.monotonic() + OUTPUT_BUDGET
        if not output_lock.acquire(timeout=OUTPUT_BUDGET):
            raise TimeoutError("response output unavailable")
        try:
            data = memoryview(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            while data:
                if transport_closed.is_set() or time.monotonic() >= deadline:
                    raise TimeoutError("response output unavailable")
                try:
                    count = os.write(output_fd, data)
                except BlockingIOError:
                    transport_closed.wait(0.02)
                    continue
                if not count:
                    transport_closed.wait(0.02)
                    continue
                data = data[count:]
        finally:
            output_lock.release()

    def reap():
        alive = []
        for thread in workers:
            if thread.is_alive():
                alive.append(thread)
            else:
                thread.join(timeout=0)
        workers[:] = alive

    def execute(raw, message, ident, cancel, slot):
        nonlocal active_work, active_control
        try:
            try:
                if cancel.is_set():
                    raise bounded.CommandCancelled("command cancelled")
                response = _dispatch(surface, raw, ident, cancel=cancel)
            except bounded.CommandCancelled:
                response = _stage_error(ident, message, "cancelled", surface)
            except DispatchFailure as exc:
                response = _stage_error(ident, message, exc.stage, surface)
                response["result"] = diagnostic_support.attach(response["result"], exc.diagnostic)
            except (bounded.CommandTimedOut, LockUnavailable):
                response = _stage_error(ident, message, "deadline", surface)
            except TimeoutError:
                raise
            except Exception:
                response = _stage_error(ident, message, "helper_failed", surface)
            with pending_lock:
                if cancel.is_set():
                    response = _stage_error(ident, message, "cancelled", surface)
                if pending.get(ident) is cancel:
                    pending.pop(ident)
            send(response)
        except TimeoutError:
            raise
        except OSError:
            transport_closed.set()
            with pending_lock:
                for event in pending.values():
                    event.set()
        finally:
            with pending_lock:
                if pending.get(ident) is cancel:
                    pending.pop(ident)
                if slot == "control":
                    active_control -= 1
                else:
                    active_work -= 1

    try:
        while not transport_closed.is_set():
            raw = _read_mcp_line(stdin)
            if raw is False:
                break
            if raw is None or not raw.strip():
                continue
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if method == "notifications/cancelled":
                params = message.get("params") if isinstance(message.get("params"), dict) else {}
                request_id = params.get("requestId")
                if _valid_rpc_id(request_id):
                    with pending_lock:
                        if (event := pending.get(request_id)) is not None:
                            event.set()
                continue
            if "id" not in message:
                continue
            ident = message["id"]
            if not _valid_rpc_id(ident):
                send(dict(jsonrpc="2.0", id=None, error=dict(code=-32600, message="Invalid request id")))
                continue
            if (local := _local_answer(message)) is not None:
                with pending_lock:
                    duplicate = ident in pending
                send(_front_reject(ident, "Duplicate pending request; not executed.") if duplicate else local)
                continue
            if method not in {"tools/list", "tools/call"}:
                send(dict(jsonrpc="2.0", id=ident, error=dict(code=-32601, message="Method not found")))
                continue
            is_control = method == "tools/call" and _local_tool_name(message) in CONTROL_TOOLS
            slot = None
            cancel = None
            with pending_lock:
                reap()
                if ident in pending:
                    duplicate = True
                else:
                    duplicate = False
                    if is_control:
                        if active_work < WORK_LIMIT:
                            slot = "work"
                        elif active_control < CONTROL_LIMIT:
                            slot = "control"
                    elif active_work < WORK_LIMIT:
                        slot = "work"
                    if slot == "work":
                        active_work += 1
                    elif slot == "control":
                        active_control += 1
                    if slot:
                        cancel = threading.Event()
                        pending[ident] = cancel
            if duplicate:
                send(_front_reject(ident, "Duplicate pending request; not executed."))
                continue
            if slot is None:
                send(_front_reject(ident, "Request capacity reached; not executed."))
                continue
            thread = threading.Thread(
                target=execute,
                args=(raw, message, ident, cancel, slot),
                name=f"mindie-mcp-{ident}",
                daemon=True,
            )
            thread.start()
            workers.append(thread)
    finally:
        transport_closed.set()
        with pending_lock:
            events = list(pending.values())
        for event in events:
            event.set()
        deadline = time.monotonic() + EOF_JOIN
        for thread in list(workers):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            thread.join(timeout=remaining)
    return 0


def _updater(rest) -> int:
    try:
        current = _current(_state_dir())
    except TimeoutError:
        raise
    except Exception as exc:
        print(f"no committed generation: {exc}"[:400], file=sys.stderr)
        return 2
    script = Path(current["generation"]) / "scripts" / "updater.py"
    if not script.is_file():
        diagnostic_support.failure("updater", "helper_missing", "missing_committed_file")
        print("committed generation lacks updater.py", file=sys.stderr)
        return 2
    try:
        if os.name == "nt":
            import subprocess

            return subprocess.run(
                [current["python"], str(script), *rest],
                env=_child_env(current), timeout=1210, check=False,
            ).returncode
        os.execve(current["python"], [current["python"], str(script), *rest], _child_env(current))
    except TimeoutError:
        raise
    except OSError:
        diagnostic_support.failure("updater", "helper_failed", "child_failure")
        print("committed generation updater could not start", file=sys.stderr)
        return 2
    return 2


def main(argv=None) -> int:
    global _CONFIG_FILE
    argv = list(sys.argv[1:] if argv is None else argv)
    _CONFIG_FILE = None
    if len(argv) >= 2 and argv[0] == "--config":
        _CONFIG_FILE = argv[1]
        os.environ["MINDIE_KIMI_CONFIG"] = _CONFIG_FILE
        argv = argv[2:]
    kind = argv[0] if argv else ""
    name = argv[1] if len(argv) > 1 else ""
    if kind == "hook" and name in {"pretool", "stop"}:
        return _hook(name)
    if kind == "mcp" and name in {"knowledge", "remote", "components"}:
        return _mcp(name)
    if kind == "updater":
        return _updater(argv[1:])
    if kind == "hook":
        return _fail_open()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
