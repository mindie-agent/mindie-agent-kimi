#!/usr/bin/env python3
"""Adapter-owned MCP server for `knowledge` and `remote` surfaces in Kimi.

[Role]
Serves JSON-RPC 2.0 MCP requests (`initialize`, `ping`, `tools/list`,
`tools/call`) for `mindie_entry`, `knowledge_query`, `knowledge_explain`,
`knowledge_feedback`, `knowledge_distill`, and `remote-dev` tools.

[Invariants]
- Native task identity is never accepted from tool arguments (`sessionId`,
  `thread_id`, `session_id`, `_session_id`, `_session_verified`, `_activation`,
  and `mindie_activation` on the `knowledge` surface are rejected).
- Every `tools/call` requires a fresh `request_nonce` bound by native
  `PreToolUse` and claimed atomically before execution (`claim_nonce`).
- Explicit knowledge domains select only the trusted install-level
  `domain_configs` engine path; arbitrary paths and maps never enter tool
  arguments. Unknown or conflicting domains fail instead of using the default.
- Distillation requires an active capture lease from the selected target
  engine's own admission store; default-domain authority is never forwarded.
- Remote failures preserve `submission_state` (`not_sent`, `uncertain`,
  `acknowledged`) and job references so callers never blindly replay writes.
  Trusted local caller/`not_sent` failures also preserve the shared bounded
  `input_repair` (`missing` fields and action) in `structuredContent` and
  model-visible text; exception prose, stderr, paths and credentials are never
  copied into that projection.
- Only the successful nonce claimant finalizes its receipt. Accounting failures
  add `bind_warning` and `diagnostic_ref` without changing the tool outcome.

[Timeouts & Locks]
- Clamps `remote-dev` `yield_time_ms` to `MAX_YIELD_TIME_MS = 30000` (30s).
- Distill forwarding uses the shared `distill_selected` summary budget plus
  ledger/cleanup reserve, never the query deadline or an unbounded timeout.
- Nonce claim uses SQLite `BEGIN IMMEDIATE` (`busy_timeout=5000`).

[Side-Effect Policy]
- Persists nonce claim/completion in `mcp-binds.sqlite3`, dispatches knowledge
  RPCs to the selected local knowledge service, and creates
  `<state_dir>/remote/<session>` directories for `remote-dev` calls.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from identity import claim_nonce, finish_nonce, local_tool_name, require_nonce
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
            "or status; an optional configured domain binds that target domain "
            "separately without replacing the default-domain lease. op=choose "
            "configures contribution or explicit disable "
            "(contribute/disabled) for knowledge sharing and sanitized fault reporting. A conversational contribution "
            "naming the public repository and account enables sharing for the "
            "current project; via the entry itself the destination comes from "
            "the native arguments (/mindie-agent contribute owner/repo "
            "account). A saved choice persists across sessions, "
            "restarts, upgrades and failures. Legacy choices require explicit "
            "confirmation before fault uploads; ordinary startup never grants consent. "
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
                        "recover", "reporting-status",
                    ],
                },
                request_nonce=NONCE_PROP,
                choice={"type": "string", "enum": ["contribute", "disabled"]},
                repository={"type": "string", "description": "Public owner/repo the user stated for contribution."},
                account={"type": "string", "description": "Public account name the user stated for contribution."},
                domain={
                    "type": "string",
                    "description": "Installed canonical target domain to bind additionally; valid only with op=init.",
                },
                arguments={"type": "string"},
            ),
            required=["op", "request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_query",
        description=(
            "Search graded rules and task material in the selected domain. Returns bounded navigation "
            "cards plus foundation_cards (compatible results also follow); each card has a readable ref "
            "and feedback_ref for its observed revision. Pass known task conditions to filter graded rules. "
            "For related task blocks, pass related_next as continuation without query. "
            "A changed corpus rejects the continuation. Archived or superseded units stay hidden unless "
            "include_archived is true. Pass domain only when the install configures that domain; an unknown "
            "domain fails instead of using the default. Set navigation_only for cards without inline bodies, "
            "and pass run_id to record managed-run delivery. References are advisory."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                query={"type": "string"},
                limit={"type": "integer", "minimum": 1, "maximum": 20},
                conditions={"type": "object"},
                continuation={"type": "string"},
                domain={
                    "type": "string",
                    "description": "Installed canonical domain to search; omit for the default domain.",
                },
                domain_path={"type": "string"},
                token_budget={"type": "integer", "minimum": 1, "maximum": 16000},
                include_unknown={"type": "boolean"},
                include_archived={"type": "boolean"},
                navigation_only={
                    "type": "boolean",
                    "description": "Return navigation cards only; omit inline result bodies.",
                },
                run_id={
                    "type": "string",
                    "description": "Managed run id used to record deduplicated discovered/loaded delivery events.",
                },
                **rsi_explore.schema_properties(),
                request_nonce=NONCE_PROP,
            ),
            required=["request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_distill",
        description=(
            "Distill explicitly selected, already authorized evidence into graded "
            "knowledge. Requires an active /mindie-agent task in the selected domain, "
            "a fresh request_nonce, and an explicit task_state; ordinary Stop is not "
            "completion. Inputs are the selected sanitized evidence only. Optional "
            "related_refs select existing units for delta comparison, and proposal "
            "submits a mindie-distill-decision/1 foreground decision for deterministic "
            "validation without a model call. The shared runtime owns redaction, "
            "SummaryLedger, local admission, and any publication plan."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                inputs={
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "description": (
                                    "experiment_ledger | run_manifest | authorized_transcript | "
                                    "ecosystem_repo | hardware_spec | ablation_receipt"
                                ),
                            },
                            "outcome": {
                                "type": "string",
                                "description": (
                                    "observed | verified | compile_failed | infra_failed | "
                                    "measurement_invalid | in_flight"
                                ),
                            },
                            "source_locator": {"type": "string"},
                            "content": {"type": "string"},
                            "evidence_refs": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "repetitions": {"type": "integer", "minimum": 1},
                        },
                        "required": [
                            "kind", "outcome", "source_locator", "content", "evidence_refs",
                        ],
                        "additionalProperties": False,
                    },
                },
                task_state={
                    "type": "string",
                    "enum": ["in_flight", "verified_milestone", "completed"],
                },
                related_refs={
                    "type": "array",
                    "items": {"type": "string"},
                },
                proposal={"type": "object"},
                domain={
                    "type": "string",
                    "description": "Installed canonical domain to distill into; omit for the default domain.",
                },
                request_nonce=NONCE_PROP,
            ),
            required=["inputs", "task_state", "request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description=(
            "Read one reference or a batch of graded references. Use the cards returned by "
            "knowledge_query for navigation, then load only the selected units. Pass ref or "
            "refs with observed conditions, known_refs that are already fully visible, and an "
            "optional token_budget. Shared prerequisites expand once in requires; primary units "
            "return complete units. A single ref keeps the exact current revision and legacy "
            "task/block navigation; a block ref returns that immutable block with "
            "previous_block_ref/next_block_ref for walking the task. Pass run_id to record delivery "
            "for a managed run. The content is a reference "
            "slice, not the full case or a verified report."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                refs={"type": "array", "items": {"type": "string"}},
                conditions={"type": "object"},
                known_refs={"type": "array", "items": {"type": "string"}},
                token_budget={"type": "integer", "minimum": 1, "maximum": 16000},
                run_id={
                    "type": "string",
                    "description": "Managed run id used to record explain delivery for this read.",
                },
                **rsi_explore.schema_properties(),
                request_nonce=NONCE_PROP,
            ),
            required=["request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_feedback",
        description=(
            "Record optional usage evidence for the exact feedback_ref returned by query/explain. "
            "Keep rating=up|down as the legacy vote; rating is optional when event_type is supplied. "
            "event_type tracks discovered -> loaded -> applied -> evaluated; evaluated requires a "
            "run_id and outcome=supported|refuted|inconclusive. step_id binds the stage to a step. "
            "evidence_source records the caller-declared strength of evaluated evidence. Never required."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                rating={"type": "string", "enum": ["up", "down"]},
                reason={"type": "string"},
                event_type={
                    "type": "string",
                    "enum": ["discovered", "loaded", "applied", "evaluated"],
                },
                run_id={"type": "string"},
                step_id={"type": "string"},
                outcome={
                    "type": "string",
                    "enum": ["supported", "refuted", "inconclusive"],
                },
                evidence_source={
                    "type": "string",
                    "enum": [
                        "agent_declared",
                        "run_manifest_verified",
                        "ablation_verified",
                    ],
                    "description": "Caller-declared evidence source for this event; defaults to agent_declared.",
                },
                request_nonce=NONCE_PROP,
            ),
            required=["ref", "request_nonce"],
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
        repair = details.get("input_repair")
        # Only the shared caller classification is trusted to describe a local
        # pre-dispatch correction. Transport and uncertain outcomes never gain
        # replay instructions here.
        if (structured["category"] == "caller"
                and structured.get("submission_state") == "not_sent"
                and isinstance(repair, dict)):
            structured["input_repair"] = repair
            structured["remote_outcome"] = "not_sent"
    key, value = _validated_job_ref(args)
    if key:
        structured[key] = value
    repair = structured.get("input_repair")
    if isinstance(repair, dict):
        missing = [item for item in repair.get("missing", ()) if isinstance(item, str)]
        action = repair.get("action") if isinstance(repair.get("action"), str) else None
        # Render only bounded shared repair metadata; never exception prose.
        text = f"Remote request was not sent ({stage})."
        if missing:
            text += f" Missing required argument(s): {', '.join(missing)}."
        if action:
            text += f" {action}"
    elif key:
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


_TERMINAL_COMMAND_STATES = {"succeeded", "failed", "timeout", "cancelled"}
_COMMAND_TOOLS = {
    "remote_Bash", "remote_bash", "remote_TaskOutput",
    "remote_exec_command", "remote_write_stdin", "remote_shell",
}


def _observed_exit_code(result):
    """Find a native exit code without inventing one for helper failures."""
    if not isinstance(result, dict):
        return None
    for value in (
        result.get("exit_code"),
        (result.get("task") or {}).get("exitCode") if isinstance(result.get("task"), dict) else None,
    ):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    for container in (result.get("job"), result.get("remote_status")):
        if not isinstance(container, dict):
            continue
        value = container.get("exit_code")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        for nested in (container.get("result"), container.get("remote_status")):
            if isinstance(nested, dict) and isinstance(nested.get("exit_code"), int) \
                    and not isinstance(nested.get("exit_code"), bool):
                return nested["exit_code"]
    return None


def _completed_command(name, result):
    if name not in _COMMAND_TOOLS or not isinstance(result, dict):
        return False
    error_details = result.get("error_details")
    if result.get("unknown") or (
            isinstance(error_details, dict)
            and error_details.get("submission_state") == "uncertain"):
        return False
    if (result.get("state") or result.get("status")) not in _TERMINAL_COMMAND_STATES:
        return False
    # A terminal state alone is not proof that the command ran: a failed
    # start/status RPC has no observed exit code. Require the real observation.
    return _observed_exit_code(result) is not None


def remote_result(name, payload):
    """Preserve native content blocks and a completed nonzero command."""
    result_payload = payload.get("result", payload) if isinstance(payload, dict) else payload
    text = payload.get("text") if isinstance(payload, dict) else None
    blocks = [dict(
        type="text",
        text=text if isinstance(text, str) else canonical(payload),
    )]
    native = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(native, list):
        blocks.extend(item for item in native
                      if isinstance(item, dict) and isinstance(item.get("type"), str))
    if isinstance(result_payload, dict):
        error_details = result_payload.get("error_details")
        uncertain = (
            result_payload.get("status") == "submission_uncertain"
            or result_payload.get("unknown")
            or (isinstance(error_details, dict)
                and error_details.get("submission_state") == "uncertain")
        )
    else:
        uncertain = False
    if uncertain:
        is_error = True
    elif _completed_command(name, result_payload):
        is_error = False
    elif isinstance(result_payload, dict):
        outcome = result_payload.get("outcome")
        if result_payload.get("status") == "local_recording_failed":
            completed_locally = (
                result_payload.get("operation_completed") is True
                or (isinstance(result_payload.get("error_details"), dict)
                    and result_payload["error_details"].get("operation_completed") is True)
            )
            if completed_locally:
                outcome = result_payload.get("execution_outcome", outcome)
        is_error = outcome not in {None, "success", "cancelled"}
    else:
        is_error = False
    return dict(content=blocks, structuredContent=result_payload, isError=is_error)


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
    if name in {"knowledge_query", "knowledge_distill"}:
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


def remote_tools():
    bind_remote_profile()
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
    import diagnostic_support

    diagnostic_support.bind_environment()
    # Trusted plugin binding for this MCP process. The launcher injects the
    # same literal value; tool arguments can never select or override it.
    bind_remote_profile()
    from remote_dev.mcp.schemas import ALIASES
    from remote_dev.mcp.tools import call_tool

    root = state_dir() / "remote" / session
    root.mkdir(parents=True, exist_ok=True)
    os.environ["REMOTE_DEV_SESSION_ID"] = session
    os.environ["REMOTE_DEV_STATE_DIR"] = str(root)
    # The native host calls a server-qualified name such as
    # `mcp__plugin-mindie-agent_remote__remote_Read`; remote-dev dispatch takes
    # the local wire name after the trusted nonce has already been claimed.
    local = local_tool_name(name) if isinstance(name, str) else name
    return call_tool(ALIASES.get(local, local), clamp_remote_args(args))


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
                serverInfo={"name": f"mindie-kimi-{surface}", "version": "0.1.2"},
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
    claimed = False
    succeeded = False
    result = {}
    try:
        if not isinstance(args, dict):
            raise ValueError("invalid tool arguments")
        reject_native_identity_args(args, surface)
        nonce = require_nonce(args.get("request_nonce"))
        session = claim_nonce(nonce, name, args)
        claimed = True
        _, body = strip_nonce(args, surface)
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
            result = dict(content=[dict(type="text", text=canonical(payload))], structuredContent=payload, isError=is_error)
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
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, ImportError) as exc:
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
                    bind_warning=warning, diagnostic_ref=diagnostic,
                )
                result["content"] = [*result.get("content", []), dict(type="text", text=warning)]
            except (OSError, sqlite3.Error) as exc:
                from diagnostic_support import failure as record_failure

                diagnostic = record_failure("mcp.call", "finish_nonce", "local_storage", exception=exc)
                warning = "Local bind receipt could not be saved. The tool result is unchanged; do not repeat the call."
                result["structuredContent"] = dict(
                    result.get("structuredContent") or {},
                    bind_warning=warning, diagnostic_ref=diagnostic,
                )
                result["content"] = [*result.get("content", []), dict(type="text", text=warning)]


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
    import diagnostic_support

    diagnostic_support.bind_environment()
    argv = list(sys.argv[1:] if argv is None else argv)
    once = "--once" in argv
    args = [item for item in argv if item != "--once"]
    surface = args[0] if args else "knowledge"
    if surface not in {"knowledge", "remote"}:
        raise SystemExit("surface must be knowledge or remote")
    if surface == "remote":
        bind_remote_profile()
    if once:
        serve_once(surface)
    else:
        serve(surface)


if __name__ == "__main__":
    main()
