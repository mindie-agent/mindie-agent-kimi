#!/usr/bin/env python3
"""Adapter-owned MCP server for `knowledge` and `remote` surfaces in Kimi.

[Role]
Serves JSON-RPC 2.0 MCP requests (`initialize`, `ping`, `tools/list`,
`tools/call`) for `mindie_entry`, `knowledge_query`, `knowledge_explain`,
`knowledge_feedback`, and `remote-dev` tools.

[Invariants]
- Native task identity is never accepted from tool arguments (`sessionId`,
  `thread_id`, or `session_id` on the `knowledge` surface are rejected).
- Every `tools/call` requires a fresh `request_nonce` bound by native
  `PreToolUse` and claimed atomically before execution (`claim_nonce`).
- Remote failures preserve `submission_state` (`not_sent`, `uncertain`,
  `acknowledged`) and job references so callers never blindly replay writes.

[Timeouts & Locks]
- Clamps `remote-dev` `yield_time_ms` to `MAX_YIELD_TIME_MS = 30000` (30s).
- Nonce claim uses SQLite `BEGIN IMMEDIATE` (`busy_timeout=5000`).

[Side-Effect Policy]
- Persists nonce claim/completion in `mcp-binds.sqlite3`, dispatches knowledge
  RPCs to the local knowledge service, and creates `<state_dir>/remote/<session>`
  directories for `remote-dev` calls.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import claim_nonce, finish_nonce, require_nonce
from paths import load_engine_config, state_dir

MAX_YIELD_TIME_MS = 30000
REMOTE_ERROR_CATEGORIES = frozenset({
    "internal", "caller", "validation", "permission", "remote_execution",
    "cancelled", "cancel_unconfirmed", "cleanup", "configuration",
    "process_start", "connection_capacity", "connection_timeout",
    "connection_unavailable", "rpc_disconnected", "rpc_send", "rpc_timeout",
    "command_exit", "command_protocol", "command_timeout", "remote_worker",
    "worker_capacity",
})
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
NONCE_PROP = {
    "type": "string",
    "minLength": 8,
    "maxLength": 128,
    "description": "Fresh per-call nonce. Native PreToolUse binds it; do not pass a session id.",
}

KNOWLEDGE_TOOLS = [
    dict(
        name="mindie_entry",
        description=(
            "Native MindIE entry. Mutations require the current /mindie-agent "
            "invocation; op=status can diagnose an already bound task. "
            "Requires a fresh request_nonce. Never pass a session id. "
            "op=init binds this task internally and reports missing configuration "
            "or status; op=choose configures contribution or explicit disable "
            "(contribute/disabled) and optionally the "
            "independent reporting choice. A conversational contribution "
            "naming the public repository and account enables sharing for the "
            "current project; via the entry itself the destination comes from "
            "the native arguments (/mindie-agent contribute owner/repo "
            "account). A saved choice persists across sessions, "
            "restarts, upgrades and failures and is never re-asked. "
            "Status includes safe task failure categories and contribution batch IDs for recover inspection; it never retries work."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                op={
                    "type": "string",
                    "enum": [
                        "init", "choose", "status", "deactivate",
                        "sharing-enable", "sharing-disable", "sharing-status",
                        "recover", "reporting-status", "reporting-enable", "reporting-disable",
                    ],
                },
                request_nonce=NONCE_PROP,
                choice={"type": "string", "enum": ["contribute", "disabled"]},
                repository={"type": "string", "description": "Public owner/repo the user stated for contribution."},
                account={"type": "string", "description": "Public account name the user stated for contribution."},
                reporting={"type": "string", "enum": ["enabled", "disabled", "later"]},
                arguments={"type": "string"},
            ),
            required=["op", "request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_query",
        description=(
            "Search the selected domain's knowledge and experience. Each match has a "
            "readable block ref and a feedback_ref. For more related matches, pass "
            "related_next as continuation without query. A changed corpus rejects "
            "the continuation. References are advisory."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                query={"type": "string"},
                limit={"type": "integer", "minimum": 1, "maximum": 20},
                conditions={"type": "object"},
                continuation={"type": "string"},
                request_nonce=NONCE_PROP,
            ),
            required=["request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description=(
            "Read one domain reference: a task ref returns the current navigation and "
            "first_block_ref; a block ref returns that immutable block with "
            "previous_block_ref/next_block_ref for walking the task. "
            "The content is a reference slice, not the full case or a verified report."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                request_nonce=NONCE_PROP,
            ),
            required=["ref", "request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_feedback",
        description="Optional up/down vote for a consulted reference. Never required.",
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                rating={"type": "string", "enum": ["up", "down"]},
                reason={"type": "string"},
                request_nonce=NONCE_PROP,
            ),
            required=["ref", "rating", "request_nonce"],
            additionalProperties=False,
        ),
    ),
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def send(message):
    sys.stdout.buffer.write((canonical(message) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def failure(exc):
    from diagnostic_support import attach

    text = f"Unavailable: {exc}. Continue independently."[:500]
    return attach(dict(content=[dict(type="text", text=text)], isError=True), getattr(exc, "mindie_diagnostic", None))


def _validated_job_ref(args):
    if not isinstance(args, dict):
        return None, None
    for key in ("job_id", "session_id"):
        value = args.get(key)
        if isinstance(value, str) and JOB_ID_RE.fullmatch(value):
            return key, value
    return None, None


def remote_stage_failure(args, name, stage, exc=None):
    structured = {"stage": stage, "remote_outcome": "unconfirmed"}
    if exc is not None:
        from remote_dev.core.errors import error_details

        details = error_details(exc)
        category = details.get("category")
        structured["category"] = category if isinstance(category, str) and category in REMOTE_ERROR_CATEGORIES else "internal"
        certainty = details.get("submission_state")
        if isinstance(certainty, str) and certainty in {"not_sent", "uncertain", "acknowledged"}:
            structured["submission_state"] = certainty
        if type(details.get("retryable")) is bool:
            structured["retryable"] = details["retryable"]
    key, value = _validated_job_ref(args)
    if key:
        structured[key] = value
        text = f"Remote outcome unconfirmed ({stage}). Inspect or stop original {key} {value} before a deliberate repeat."
    elif name == "remote_bash":
        if structured.get("submission_state") == "not_sent":
            structured["remote_outcome"] = "not_sent"
            text = f"Remote request was not sent ({stage})."
        else:
            text = f"Remote submission unconfirmed ({stage}). Inspect local remote-dev job records before a deliberate repeat."
    else:
        text = f"Unavailable ({stage}). Continue independently."
    for k in ("category", "submission_state"):
        if k in structured:
            text += f" {k}={structured[k]}."
    from diagnostic_support import attach

    result = dict(content=[dict(type="text", text=text[:500])], isError=True, structuredContent=structured)
    return attach(result, getattr(exc, "mindie_diagnostic", None))


def clamp_remote_args(args):
    body = dict(args)
    if "yield_time_ms" in body:
        yield_ms = body["yield_time_ms"]
        if type(yield_ms) is not int:
            raise ValueError("yield_time_ms must be an integer")
        body["yield_time_ms"] = min(yield_ms, MAX_YIELD_TIME_MS)
    return body


def reject_native_identity_args(args, surface):
    if "sessionId" in args or "thread_id" in args or ("session_id" in args and surface != "remote"):
        raise ValueError("tool arguments must not include native session identity")


def strip_nonce(args, surface=None):
    args = dict(args)
    nonce = require_nonce(args.pop("request_nonce", None))
    reject_native_identity_args(args, surface)
    return nonce, args


def knowledge_call(name, args, session):
    from knowledge_service import ensure_service, existing_service
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import rpc
    from paths import admission_path, engine_config_path

    if name == "knowledge_query":
        from mindie_knowledge.materials.provenance import query_request

        query_request(args.get("query"), args.get("limit", 5), args.get("conditions"), args.get("continuation"))
    engine = load_engine_config()
    if Admission(admission_path(engine)).active_lease(session) is None:
        raise ValueError(
            "this task is not bound yet; invoke /mindie-agent once in this "
            "task — the saved install-level choice is reused, nothing is re-asked"
        )
    method = name.removeprefix("knowledge_")
    try:
        connection = existing_service(engine_config_path())
    except (OSError, ValueError, RuntimeError):
        connection = ensure_service(engine_config_path())
    return rpc(connection, method, dict(args, _session_id=session, _session_verified=True), timeout=None)


def remote_tools():
    from remote_dev.mcp.tools import list_tools

    tools = []
    for item in list_tools():
        schema = dict(item.get("inputSchema") or {"type": "object", "properties": {}})
        properties = {**(schema.get("properties") or {}), "request_nonce": NONCE_PROP}
        required = list(schema.get("required") or [])
        if "request_nonce" not in required:
            required.append("request_nonce")
        tools.append(
            dict(
                name=item["name"],
                description=item.get("description", item["name"]),
                inputSchema=dict(schema, properties=properties, required=required),
            )
        )
    return tools


def remote_call(name, args, session):
    from remote_dev.mcp.schemas import ALIASES
    from remote_dev.mcp.tools import call_tool

    root = state_dir() / "remote" / session
    root.mkdir(parents=True, exist_ok=True)
    os.environ["REMOTE_DEV_SESSION_ID"] = session
    os.environ["REMOTE_DEV_STATE_DIR"] = str(root)
    return call_tool(ALIASES.get(name, name), clamp_remote_args(args))


def handle(surface, message):
    if "id" not in message:
        return None
    method = message.get("method")
    ident = message["id"]
    if method == "initialize":
        return dict(
            jsonrpc="2.0", id=ident,
            result=dict(
                protocolVersion="2025-11-25",
                capabilities={"tools": {}},
                serverInfo={"name": f"mindie-kimi-{surface}", "version": "0.0.2"},
            ),
        )
    if method == "ping":
        return dict(jsonrpc="2.0", id=ident, result={})
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        tools = KNOWLEDGE_TOOLS if surface == "knowledge" else remote_tools()
        return dict(jsonrpc="2.0", id=ident, result=dict(tools=tools))
    if method != "tools/call":
        return dict(jsonrpc="2.0", id=ident, error=dict(code=-32601, message="Method not found"))
    params = message.get("params") or {}
    name = params.get("name")
    args = params.get("arguments") or {}
    nonce = None
    succeeded = False
    try:
        if not isinstance(args, dict):
            raise ValueError("invalid tool arguments")
        reject_native_identity_args(args, surface)
        nonce = require_nonce(args.get("request_nonce"))
        session = claim_nonce(nonce, name, args)
        _, body = strip_nonce(args, surface)
        if surface == "knowledge" and name == "mindie_entry":
            from entry import dispatch
            from identity import session_cwd

            try:
                cwd = session_cwd(session)
            except Exception:
                cwd = None
            payload = dispatch(
                session, cwd, body.get("op"), body.get("arguments") or "",
                body.get("choice"), repository=body.get("repository"),
                account=body.get("account"), reporting=body.get("reporting"),
            )
            result = dict(content=[dict(type="text", text=canonical(payload))], structuredContent=payload, isError=False)
        elif surface == "knowledge":
            payload = knowledge_call(name, body, session)
            result = dict(content=[dict(type="text", text=canonical(payload))], structuredContent=payload, isError=False)
        else:
            try:
                payload = remote_call(name, body, session)
            except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
                return dict(jsonrpc="2.0", id=ident, result=remote_stage_failure(body, name, "helper_failed", exc))
            text = payload.get("text") if isinstance(payload, dict) else canonical(payload)
            result = dict(
                content=[dict(type="text", text=text if isinstance(text, str) else canonical(payload))],
                structuredContent=payload.get("result", payload) if isinstance(payload, dict) else payload,
                isError=isinstance(payload, dict) and payload.get("result", {}).get("outcome") not in {None, "success", "cancelled"},
            )
        succeeded = result.get("isError") is not True
        return dict(jsonrpc="2.0", id=ident, result=result)
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        return dict(jsonrpc="2.0", id=ident, result=failure(exc))
    finally:
        if nonce is not None:
            try:
                finish_nonce(nonce, succeeded)
            except Exception:
                pass


def serve(surface):
    stdin = sys.stdin.buffer
    while True:
        raw = stdin.readline()
        if not raw:
            return
        line = raw.rstrip(b"\r\n")
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and (response := handle(surface, message)) is not None:
            send(response)


def serve_once(surface):
    raw = sys.stdin.buffer.read()
    line, _, _ = raw.partition(b"\n")
    if not line.strip():
        return
    try:
        message = json.loads(line)
    except ValueError:
        return
    if isinstance(message, dict) and (response := handle(surface, message)) is not None:
        send(response)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in argv
    args = [item for item in argv if item != "--once"]
    surface = args[0] if args else "knowledge"
    if surface not in {"knowledge", "remote"}:
        raise SystemExit("surface must be knowledge or remote")
    if once:
        serve_once(surface)
    else:
        serve(surface)


if __name__ == "__main__":
    main()
