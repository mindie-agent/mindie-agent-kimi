#!/usr/bin/env python3
"""Adapter-owned MCP server for Kimi's component and explicit knowledge tools.

[Role]
Serves MCP discovery and nonce-bound calls for knowledge, remote-dev,
coordinator, profiler, sim, local diagnostics and factual status.

[Invariants]
- Native task identity is never accepted from tool arguments (`sessionId`,
  `thread_id`, `session_id`, `_session_id`, `_session_verified`, `_activation`,
  and `mindie_activation` on the `knowledge` surface are rejected).
- `knowledge` surface `tools/call` strictly requires a fresh `request_nonce`
  bound by native `PreToolUse` (`claim_nonce`). Deterministic surfaces (`remote`
  and `components`) treat `request_nonce` as an opportunistic hint: when bound,
  it supplies native session/cwd and rejects replay; when omitted or unbound
  (e.g. subagents, automations, or direct MCP calls), execution proceeds under
  `_CONNECTION_SESSION_ID` without pre-call session-binding failure.
- Knowledge domains select trusted `domain_configs` paths; arbitrary paths
  and unknown or conflicting domains are rejected.
- Distillation requires an active capture lease from the selected target
  engine's own admission store; default-domain authority is never forwarded.
- Remote failures preserve `submission_state` (`not_sent`, `uncertain`,
  `acknowledged`) and job references so callers never blindly replay writes.
  Caller/`not_sent` failures retain bounded input_repair without private prose.
- Only the successful nonce claimant finalizes its receipt. Accounting failures
  add `bind_warning` and `diagnostic_ref` without changing the tool outcome.

[Timeouts & Locks]
- Clamps `remote-dev` `yield_time_ms` to `MAX_YIELD_TIME_MS = 30000` (30s).
- Distill forwarding uses the shared `distill_selected` summary budget plus
  ledger/cleanup reserve, never the query deadline or an unbounded timeout.
- Nonce claim uses SQLite `BEGIN IMMEDIATE` (`busy_timeout=5000`).

[Side-Effect Policy]
- Persists nonce receipts, dispatches manually authorized knowledge calls,
  and runs explicitly requested remote operations or component CLIs.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import sqlite3
import sys
import threading
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import MissingBind, claim_nonce, finish_nonce, local_tool_name, require_nonce
from paths import (
    domain_configs,
    domain_from_ref,
    engine_config_path,
    load_engine_config_at,
    state_dir,
)
import rsi_explore

MAX_YIELD_TIME_MS = 30000
KNOWLEDGE_RPC_TIMEOUT = 30.0
# Trusted Kimi plugin binding for the remote surface. This must be in the
# process environment before remote_dev is imported: its harness catalog reads
# the profile during tools/list, not only during tools/call.
REMOTE_PROFILE = "kimi"
REMOTE_ERROR_CATEGORIES = frozenset({
    "internal", "caller", "validation", "permission", "remote_execution",
    "cancelled", "cancel_unconfirmed", "cleanup", "configuration",
    "process_start", "connection_capacity", "connection_timeout",
    "connection_unavailable", "rpc_disconnected", "rpc_send", "rpc_timeout",
    "command_exit", "command_protocol", "command_timeout", "remote_worker",
    "worker_capacity",
})
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
_CONNECTION_SESSION_ID = os.environ.get("MINDIE_MCP_CONNECTION_ID") or f"kimi-mcp-{os.getpid()}-{uuid.uuid4().hex[:12]}"
from mcp_catalog import (
    JOB_ID_RE,
    KNOWLEDGE_TOOLS,
    MAX_YIELD_TIME_MS,
    NONCE_PROP,
    REMOTE_ERROR_CATEGORIES,
    clamp_remote_args,
    has_any_active_knowledge_lease as _has_any_active_knowledge_lease,
    remote_result,
    remote_stage_failure,
)


_SEND_LOCK = threading.Lock()
_REMOTE_ENV_LOCK = threading.Lock()
_READ_ONLY_TOOLS = frozenset({
    "remote_read", "remote_Read", "remote_glob", "remote_Glob", "remote_grep", "remote_Grep",
    "remote_ReadMediaFile", "remote_ls", "remote_job_status", "remote_job_tail", "remote_job_output",
    "remote_job_stop", "remote_job_cancel", "remote_TaskOutput", "remote_TaskStop", "remote_TaskList",
    "remote_probe", "remote_context_snapshot", "mindie_status", "diagnostics_status",
    "knowledge_query", "knowledge_explain",
})


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def send(message):
    with _SEND_LOCK:
        sys.stdout.buffer.write((canonical(message) + "\n").encode("utf-8"))
        sys.stdout.buffer.flush()


def parse_rpc_line(line):
    try:
        message = json.loads(line)
    except TimeoutError:
        raise
    except ValueError as exc:
        from diagnostic_support import failure as record_failure

        record_failure("mcp.serve", "parse", "protocol", exception=exc)
        send(dict(jsonrpc="2.0", id=None, error=dict(code=-32700, message="Parse error")))
        return None
    if not isinstance(message, dict):
        from diagnostic_support import failure as record_failure

        record_failure("mcp.serve", "parse", "protocol")
        send(dict(jsonrpc="2.0", id=None, error=dict(code=-32600, message="Invalid Request")))
        return None
    return message


def failure(exc):
    from diagnostic_support import attach

    text = f"Unavailable: {exc}. Continue independently."[:500]
    return attach(dict(content=[dict(type="text", text=text)], isError=True), getattr(exc, "mindie_diagnostic", None))


def reject_native_identity_args(args, surface):
    if (
        "sessionId" in args
        or "thread_id" in args
        or ("session_id" in args and surface != "remote")
        or "_session_id" in args
        or "_session_verified" in args
        or "_activation" in args
        or "mindie_activation" in args
    ):
        raise ValueError("tool arguments must not include native session identity")


def strip_nonce(args, surface=None):
    args = dict(args)
    nonce = require_nonce(args.pop("request_nonce", None))
    reject_native_identity_args(args, surface)
    return nonce, args


def _select_engine_path(*, domain=None, ref=None):
    """Select one trusted configured engine config without exposing path arguments."""
    default = engine_config_path()
    try:
        from mindie_knowledge.loop.domain_routes import select_engine_config
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        ref_domain = domain_from_ref(ref)
        if domain is None and ref_domain is not None:
            default_engine = load_engine_config_at(default)
            if ref_domain == default_engine.get("domain"):
                return default, False
        raise ValueError(
            "knowledge domain routing is unavailable in the installed runtime; "
            "no default-domain substitution was attempted"
        ) from exc
    return select_engine_config(
        default, domain=domain, ref=ref, domain_configs=domain_configs()
    ), True


def _explain_route_ref(body):
    """Return the reference that selects the engine for a single or batch read.

    Batch refs must agree on their ``mindie://`` domain authority. The route
    follows ``ref`` when present and otherwise the first ``refs`` item; the
    complete list stays in the payload for the shared closure assembler.
    """
    ref = body.get("ref")
    refs = body.get("refs")
    if ref is not None and not isinstance(ref, str):
        raise ValueError("ref must be a reference string")
    if refs is not None:
        if not isinstance(refs, list):
            raise ValueError("refs must be an array of reference strings")
        if any(not isinstance(item, str) or not item.strip() for item in refs):
            raise ValueError("refs must contain nonempty reference strings")
    requested = list(refs or ())
    if ref is not None and ref not in requested:
        requested.insert(0, ref)
    if not requested:
        raise ValueError("knowledge_explain requires ref or refs")
    authorities = {domain_from_ref(item) for item in requested}
    authorities.discard(None)
    if len(authorities) > 1:
        raise ValueError("knowledge_explain references must not mix mindie:// domain authorities")
    return ref if ref is not None else refs[0]


def _engine_for_call(name, body):
    """Resolve the target engine before reading its admission or public data."""
    if name in {"knowledge_query", "knowledge_distill", "knowledge_sync"}:
        requested = body.pop("domain", None)
        if requested is not None:
            path, routed = _select_engine_path(domain=requested)
            return path, load_engine_config_at(path), routed
    elif name in {"knowledge_explain", "knowledge_feedback"}:
        ref = _explain_route_ref(body) if name == "knowledge_explain" else body.get("ref")
        supplied = body.pop("domain", None)
        if supplied is not None:
            path, routed = _select_engine_path(domain=supplied, ref=ref)
            return path, load_engine_config_at(path), routed
        if domain_from_ref(ref) is not None:
            path, routed = _select_engine_path(ref=ref)
            return path, load_engine_config_at(path), routed
    path = engine_config_path()
    return path, load_engine_config_at(path), False


def _knowledge_distill_call(engine_path, connection, body, session, token):
    """Forward selected evidence with optional related refs or a proposal.

    The historical paired helper accepts only the four base identity keys, so
    optional delta/proposal fields use the same authenticated service route
    directly. Calls without those fields keep their exact prior wire shape.
    """
    from mindie_knowledge.loop.cli import distill_selected, rpc

    payload = {
        "inputs": body.get("inputs"),
        "task_state": body.get("task_state"),
        "_session_id": session,
        "_activation": token,
    }
    optional = {key: body[key] for key in ("related_refs", "proposal") if key in body}
    if not optional:
        return distill_selected(engine_path, payload)
    payload.update(optional)
    from mindie_knowledge.loop import summary_budget

    return rpc(connection, "distill", payload, timeout=summary_budget.distill_rpc_timeout())


def knowledge_call(name, args, session):
    from knowledge_service import ensure_service, existing_service
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import rpc
    from paths import admission_path

    body = rsi_explore.forwarded(args) if name in {"knowledge_query", "knowledge_explain"} else dict(args)
    if name == "knowledge_query":
        # Boundary check, not the Store's duplicate: reject a malformed call
        # before claiming a lease or cold-starting the knowledge service.
        from mindie_knowledge.materials.provenance import query_request

        query_request(body.get("query"), body.get("limit", 5), body.get("conditions"), body.get("continuation"))
    engine_path, engine, routed = _engine_for_call(name, body)
    lease = Admission(admission_path(engine)).knowledge_lease(session)
    # A bound native task may read a configured public domain without granting
    # that domain contribution rights. Mutations still need its own lease.
    read_bound = False
    if lease is None and routed and name in {"knowledge_query", "knowledge_explain"}:
        default_engine = load_engine_config_at(engine_config_path())
        read_bound = Admission(admission_path(default_engine)).knowledge_lease(session) is not None
    if lease is None and not read_bound:
        domain_note = f" for domain {engine['domain']}" if routed else ""
        raise ValueError(
            f"this task is not bound yet{domain_note}; invoke /mindie-agent once in this "
            "task — the saved install-level choice is reused, nothing is re-asked"
        )
    method = name.removeprefix("knowledge_")
    if method == "sync":
        from knowledge_sync import execute

        if body:
            raise ValueError("knowledge_sync accepts only an optional domain")
        return execute(engine_path)
    token = None
    if method == "distill":
        allowed = {"inputs", "task_state", "related_refs", "proposal"}
        if not {"inputs", "task_state"} <= set(body) or not set(body) <= allowed:
            raise ValueError(
                "knowledge_distill accepts inputs, task_state and optional "
                "related_refs/proposal plus an optional domain"
            )
        token = lease.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("distill requires an active capture lease")
        try:
            from mindie_knowledge.loop.cli import distill_selected  # noqa: F401
        except ImportError as exc:
            raise ValueError("knowledge distill is unavailable in the installed runtime") from exc
    from urllib.error import URLError

    try:
        connection = existing_service(engine_path)
    except (FileNotFoundError, ConnectionRefusedError):
        connection = ensure_service(engine_path)
    except URLError as exc:
        if not isinstance(exc.reason, ConnectionRefusedError):
            raise
        connection = ensure_service(engine_path)
    if method == "distill":
        return _knowledge_distill_call(engine_path, connection, body, session, token)
    payload = dict(body, _session_id=session, _session_verified=True)
    if routed:
        payload["domain"] = engine["domain"]
    return rpc(connection, method, payload, timeout=KNOWLEDGE_RPC_TIMEOUT)


def bind_remote_profile() -> None:
    """Bind the trusted Kimi native profile before any remote_dev lookup.

    The launcher and host-package manifest also carry this value; this is the
    last-resort boundary for an older package whose manifest predates the env
    field. It never grants a caller-selectable profile.
    """
    os.environ["REMOTE_DEV_HARNESS_PROFILE"] = REMOTE_PROFILE


def _nonce_tools(catalog, *, require=True):
    tools = []
    for item in catalog:
        schema = dict(item.get("inputSchema") or {"type": "object", "properties": {}})
        properties = {**(schema.get("properties") or {}), "request_nonce": NONCE_PROP}
        required = list(schema.get("required") or [])
        if require and "request_nonce" not in required:
            required.append("request_nonce")
        tools.append(
            dict(
                item,
                name=item["name"],
                description=item.get("description", item["name"]),
                inputSchema=dict(schema, properties=properties, required=required),
            )
        )
    return tools


def remote_tools():
    bind_remote_profile()
    from remote_dev.mcp.tools import list_tools

    return _nonce_tools(list_tools(), require=False)


def component_tools():
    from component_tools import catalog

    status = dict(
        name="mindie_status",
        description="Read factual local MindIE configuration and this session's status. Does not enable knowledge or start a service.",
        inputSchema=dict(type="object", properties={}, additionalProperties=False),
        annotations=dict(readOnlyHint=True, destructiveHint=False, openWorldHint=False),
    )
    return _nonce_tools([status, *catalog()], require=False)


def knowledge_tools():
    if not _has_any_active_knowledge_lease():
        return [KNOWLEDGE_TOOLS[0]]
    from knowledge_sync import TOOL

    return [*KNOWLEDGE_TOOLS, *_nonce_tools([TOOL])]


def remote_call(name, args, session):
    import diagnostic_support

    diagnostic_support.bind_environment()
    # Trusted plugin binding for this MCP process. The launcher injects the
    # same literal value; tool arguments can never select or override it.
    bind_remote_profile()
    from remote_dev.mcp.schemas import ALIASES
    from remote_dev.mcp.tools import call_tool

    # Profile-shared remote job root (`AD-02` fixed) so subagents and reconnected
    # MCP sessions can poll/stop parent `job_id`s, while `REMOTE_DEV_SESSION_ID`
    # keeps read-before-write ledgers scoped per session.
    root = state_dir() / "remote"
    root.mkdir(parents=True, exist_ok=True)
    local = local_tool_name(name) if isinstance(name, str) else name
    with _REMOTE_ENV_LOCK:
        os.environ["REMOTE_DEV_SESSION_ID"] = session
        os.environ["REMOTE_DEV_STATE_DIR"] = str(root)
        return call_tool(ALIASES.get(local, local), clamp_remote_args(args))


def handle(surface, message):
    if "id" not in message:
        return None
    method = message.get("method")
    ident = message["id"]
    if method == "initialize":
        from diagnostic_support import plugin_version

        return dict(
            jsonrpc="2.0", id=ident,
            result=dict(
                protocolVersion="2025-11-25",
                capabilities={"tools": {"listChanged": True}},
                serverInfo={"name": f"mindie-kimi-{surface}", "version": plugin_version()},
            ),
        )
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        tools = (knowledge_tools() if surface == "knowledge" else
                 component_tools() if surface == "components" else remote_tools())
        return dict(jsonrpc="2.0", id=ident, result=dict(tools=tools))
    if method != "tools/call":
        return dict(jsonrpc="2.0", id=ident, error=dict(code=-32601, message="Method not found"))
    params = message.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    nonce = None
    claimed = False
    succeeded = False
    result = {}
    try:
        if not isinstance(args, dict):
            raise ValueError("invalid tool arguments")
        reject_native_identity_args(args, surface)
        if surface == "knowledge":
            nonce = require_nonce(args.get("request_nonce"))
            session = claim_nonce(nonce, name, args)
            claimed = True
            _, body = strip_nonce(args, surface)
        elif isinstance(args.get("request_nonce"), str) and args["request_nonce"].strip():
            nonce = require_nonce(args.get("request_nonce"))
            try:
                session = claim_nonce(nonce, name, args)
                claimed = True
            except MissingBind:
                session = _CONNECTION_SESSION_ID
            body = {k: v for k, v in args.items() if k != "request_nonce"}
        else:
            session = _CONNECTION_SESSION_ID
            body = {k: v for k, v in args.items() if k != "request_nonce"}
        if surface == "knowledge" and name == "mindie_entry":
            from entry import dispatch
            from identity import session_cwd

            try:
                cwd = session_cwd(session)
            except TimeoutError:
                raise
            except Exception:
                cwd = None
            payload = dispatch(
                session, cwd, body.get("op"), body.get("arguments") or "",
                body.get("choice"), repository=body.get("repository"),
                account=body.get("account"), domain=body.get("domain"),
            )
            result = dict(
                content=[dict(type="text", text=canonical(payload))],
                structuredContent=payload,
                isError=body.get("op") == "deactivate" and payload.get("status") == "partial",
            )
        elif surface == "knowledge":
            payload = knowledge_call(name, body, session)
            is_error = False
            if name == "knowledge_distill":
                from mindie_knowledge.graded.distill import result_view
                payload, is_error = result_view(payload)
            elif name == "knowledge_sync":
                is_error = payload["status"] != "ok"
            result = dict(content=[dict(type="text", text=canonical(payload))], structuredContent=payload, isError=is_error)
        elif surface == "components":
            if name == "mindie_status":
                from entry import status_facts

                payload = status_facts(session)
                result = dict(content=[dict(type="text", text=canonical(payload))],
                              structuredContent=payload, isError=False)
            else:
                from component_tools import execute

                result = execute(name, body, session, session_bound=claimed)
        else:
            try:
                payload = remote_call(name, body, session)
            except TimeoutError as exc:
                result = remote_stage_failure(body, name, "helper_failed", exc)
                return dict(jsonrpc="2.0", id=ident, result=result)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
                result = remote_stage_failure(body, name, "helper_failed", exc)
                return dict(jsonrpc="2.0", id=ident, result=result)
            result = remote_result(local_tool_name(name), payload)
        succeeded = result.get("isError") is not True
        return dict(jsonrpc="2.0", id=ident, result=result)
    except TimeoutError as exc:
        result = failure(exc)
        return dict(jsonrpc="2.0", id=ident, result=result)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError, AttributeError) as exc:
        result = failure(exc)
        return dict(jsonrpc="2.0", id=ident, result=result)
    finally:
        if claimed:
            try:
                finish_nonce(nonce, succeeded)
            except TimeoutError as exc:
                from diagnostic_support import failure as record_failure

                diagnostic = record_failure("mcp.call", "finish_nonce", "local_storage", exception=exc)
                warning = "Local bind receipt could not be saved. The tool result is unchanged; do not repeat the call."
                result["structuredContent"] = dict(
                    result.get("structuredContent") or {},
                    bind_warning=warning,
                    receipt_warning=warning,
                    receipt_unconfirmed=True,
                    diagnostic_ref=diagnostic,
                )
                result["content"] = [*result.get("content", []), dict(type="text", text=warning)]
            except (OSError, sqlite3.Error) as exc:
                from diagnostic_support import failure as record_failure

                diagnostic = record_failure("mcp.call", "finish_nonce", "local_storage", exception=exc)
                warning = "Local bind receipt could not be saved. The tool result is unchanged; do not repeat the call."
                result["structuredContent"] = dict(
                    result.get("structuredContent") or {},
                    bind_warning=warning,
                    receipt_warning=warning,
                    receipt_unconfirmed=True,
                    diagnostic_ref=diagnostic,
                )
                result["content"] = [*result.get("content", []), dict(type="text", text=warning)]


def _is_readonly_rpc(message: dict) -> bool:
    method = message.get("method")
    if method == "tools/list":
        return True
    if method != "tools/call":
        return False
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    raw_name = params.get("name")
    if not isinstance(raw_name, str):
        return False
    return local_tool_name(raw_name) in _READ_ONLY_TOOLS


def serve(surface):
    from native_mcp import lease_owning_runtime

    with lease_owning_runtime():
        return _serve(surface)


def _serve(surface):
    stdin = sys.stdin.buffer
    read_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="kimi-mcp-ro")
    work_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="kimi-mcp-rw")
    last_lease_state = _has_any_active_knowledge_lease() if surface == "knowledge" else None

    def _run_and_reply(msg):
        nonlocal last_lease_state
        response = handle(surface, msg)
        if response is not None:
            send(response)
        if surface == "knowledge" and msg.get("method") == "tools/call":
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            if local_tool_name(str(params.get("name") or "")) == "mindie_entry":
                now_state = _has_any_active_knowledge_lease()
                if now_state != last_lease_state:
                    last_lease_state = now_state
                    send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    try:
        while True:
            raw = stdin.readline()
            if not raw:
                return
            line = raw.rstrip(b"\r\n")
            if not line.strip():
                continue
            message = parse_rpc_line(line)
            if not isinstance(message, dict):
                continue
            method = message.get("method")
            if method in {"initialize", "ping", "notifications/initialized", "notifications/cancelled"}:
                response = handle(surface, message)
                if response is not None:
                    send(response)
                continue
            pool = read_pool if _is_readonly_rpc(message) else work_pool
            pool.submit(_run_and_reply, message)
    finally:
        read_pool.shutdown(wait=True)
        work_pool.shutdown(wait=True)


def serve_once(surface):
    raw = sys.stdin.buffer.read()
    line, _, _ = raw.partition(b"\n")
    if not line.strip():
        return
    message = parse_rpc_line(line)
    if isinstance(message, dict) and (response := handle(surface, message)) is not None:
        send(response)


def main(argv=None):
    import diagnostic_support

    diagnostic_support.bind_environment()
    argv = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in argv
    args = [item for item in argv if item != "--once"]
    surface = args[0] if args else "knowledge"
    if surface not in {"knowledge", "remote", "components"}:
        raise SystemExit("surface must be knowledge, remote or components")
    if surface == "remote":
        bind_remote_profile()
    if once:
        serve_once(surface)
    else:
        from native_mcp import serve_profile
        serve_profile(surface)


if __name__ == "__main__":
    main()
