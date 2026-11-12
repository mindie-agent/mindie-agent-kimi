"""Bounded native MCP transport and selected-generation invalidation.

[Role] Keep the native connection alive across committed runtime updates.
[Invariants] Preserve pending identity, cancellation and separate control capacity.
[Timeouts & Locks] Existing facade budgets; serialized output and 1s catalog poll.
[Side-Effect Policy] Dispatch each operation once; refresh never activates a task.
"""
import json
import os
import sys
import threading
import time
from pathlib import Path


def serve_profile(surface):
    """Cover the native source manifest as well as updater-generated packages."""
    from remote_dev.mcp.catalog_refresh import serve_selected
    import mindie_launch as launch

    def select():
        pointer = launch._state_dir() / "update/current.json"
        if pointer.exists():
            current = launch._current(launch._state_dir())
            script = Path(current["generation"]) / "scripts/mcp_server.py"
            return [current["python"], str(script), surface, "--once"], launch._child_env(current, surface)
        config = launch._load_config() or {}
        return [config.get("python") or sys.executable,
                str(Path(__file__).with_name("mcp_server.py")), surface, "--once"], {}

    return serve_selected(select)

def serve(surface: str, api) -> int:
    CONTROL_LIMIT = api.CONTROL_LIMIT
    CONTROL_TOOLS = api.CONTROL_TOOLS
    DispatchFailure = api.DispatchFailure
    EOF_JOIN = api.EOF_JOIN
    LockUnavailable = api.LockUnavailable
    OUTPUT_BUDGET = api.OUTPUT_BUDGET
    WORK_LIMIT = api.WORK_LIMIT
    _dispatch = api._dispatch
    _front_reject = api._front_reject
    _local_answer = api._local_answer
    _local_tool_name = api._local_tool_name
    _read_mcp_line = api._read_mcp_line
    _stage_error = api._stage_error
    _state_dir = api._state_dir
    _valid_rpc_id = api._valid_rpc_id
    bounded = api.bounded
    diagnostic_support = api.diagnostic_support
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

    from remote_dev.mcp.catalog_refresh import CatalogRefresh, file_revision
    refresh = CatalogRefresh(lambda: file_revision(_state_dir() / "update" / "current.json"), send)

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
            refresh.check()
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
            except TimeoutError:
                raise
            except ValueError as exc:
                diagnostic_support.failure("mcp.serve", "parse", "protocol", exception=exc)
                send(dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="Parse error")))
                continue
            if not isinstance(message, dict):
                diagnostic_support.failure("mcp.serve", "parse", "protocol")
                send(dict(jsonrpc="2.0", id=None, error=dict(code=-32600, message="Invalid Request")))
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
                if not duplicate and method == "initialize":
                    refresh.start()
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
        refresh.close()
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

