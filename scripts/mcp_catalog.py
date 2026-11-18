"""[Role] Declarative native MCP schemas for default-enabled knowledge tools.

[Invariants]
- Schemas expose knowledge_* tools out of the box without mandatory request_nonce.
- Optional request nonces carry native identity hints; tool arguments never grant authority.

[Timeouts & Locks]
- Pure module data; no locks or child processes.

[Side-Effect Policy]
- Importing the catalog performs no I/O and starts no services.
"""

import rsi_explore

NONCE_PROP = {
    "type": "string",
    "minLength": 8,
    "maxLength": 128,
    "description": "Optional per-call nonce hint. Native PreToolUse binds it when supplied; do not pass a session id.",
}

KNOWLEDGE_TOOLS = [
    dict(
        name="mindie_entry",
        description=(
            "Native MindIE session and configuration helper for Kimi Code. "
            "op='init' binds this session (or an optional configured target domain); "
            "op='choose' configures contribution ('contribute' or 'disabled') for "
            "public knowledge sharing and sanitized fault reporting; op='status' "
            "returns read-only session, sharing, diagnostic, and reporting status."
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
            required=["op"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_query",
        description=(
            "Search graded rules and task material in the selected domain when a missing "
            "contract, mechanism, or method is needed for the next step. Returns bounded "
            "navigation cards and deduplicated foundation_cards; each card includes a "
            "readable ref and feedback_ref for its observed revision. Pass observed task "
            "conditions to filter graded rules (include_archived discovers archived or "
            "superseded units). Pass related_next as continuation for related task blocks, "
            "navigation_only to omit inline bodies, and run_id to bind delivery events."
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
                token_budget={"type": "integer", "minimum": 1, "maximum": 64000},
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
            required=[],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_distill",
        description=(
            "Distill caller-selected, authorized evidence into graded knowledge units "
            "at task_state='verified_milestone' or 'completed'. Pass related_refs to "
            "compare against existing units as a delta, or pass a mindie-distill-decision/1 "
            "proposal for deterministic validation and local application with zero model calls."
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
            required=["inputs", "task_state"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_explain",
        description=(
            "Read complete graded units or task/block references by their exact returned ref. "
            "For graded units, pass refs (even for one unit) with observed conditions, visible "
            "known_refs, and token_budget to assemble the dependency closure (shared prerequisites "
            "expand once in requires and full unit bodies appear in units). A single ref supports "
            "legacy task and block navigation (first_block_ref and previous_block_ref/next_block_ref "
            "for walking a task reference slice). Pass run_id to bind delivery events."
        ),
        inputSchema=dict(
            type="object",
            properties=dict(
                ref={"type": "string"},
                refs={"type": "array", "items": {"type": "string"}},
                conditions={"type": "object"},
                known_refs={"type": "array", "items": {"type": "string"}},
                token_budget={"type": "integer", "minimum": 1, "maximum": 64000},
                run_id={
                    "type": "string",
                    "description": "Managed run id used to record explain delivery for this read.",
                },
                **rsi_explore.schema_properties(),
                request_nonce=NONCE_PROP,
            ),
            required=[],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_feedback",
        description=(
            "Record optional usage events (discovered, loaded, applied, evaluated) or an "
            "up/down rating for an exact feedback_ref returned by query or explain. "
            "evaluated requires run_id and outcome=supported|refuted|inconclusive, with "
            "optional step_id and evidence_source."
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
            required=["ref"],
            additionalProperties=False,
        ),
    ),
]

import json
import os
from pathlib import Path
import re
import sqlite3

MAX_YIELD_TIME_MS = 30000
JOB_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,95}$")
REMOTE_ERROR_CATEGORIES = frozenset({
    "internal", "caller", "validation", "permission", "remote_execution",
    "cancelled", "cancel_unconfirmed", "cleanup", "configuration",
    "process_start", "connection_capacity", "connection_timeout",
    "connection_unavailable", "rpc_disconnected", "rpc_send", "rpc_timeout",
    "command_exit", "command_protocol", "command_timeout", "remote_worker",
    "worker_capacity",
})
_TERMINAL_COMMAND_STATES = {"succeeded", "failed", "timeout", "cancelled"}
_COMMAND_TOOLS = {
    "remote_Bash", "remote_bash", "remote_TaskOutput",
    "remote_exec_command", "remote_write_stdin", "remote_shell",
}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def clamp_remote_args(args):
    body = dict(args)
    if "yield_time_ms" in body:
        yield_ms = body["yield_time_ms"]
        if type(yield_ms) is not int:
            raise ValueError("yield_time_ms must be an integer")
        body["yield_time_ms"] = min(yield_ms, MAX_YIELD_TIME_MS)
    return body


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
        from remote_dev.core.errors import AUTH_ERROR_HINTS, error_details, safe_auth_error_details

        details = error_details(exc)
        category = details.get("category")
        structured["category"] = (
            category
            if isinstance(category, str) and category in REMOTE_ERROR_CATEGORIES | AUTH_ERROR_HINTS.keys()
            else "internal"
        )
        certainty = details.get("submission_state")
        if isinstance(certainty, str) and certainty in {"not_sent", "uncertain", "acknowledged"}:
            structured["submission_state"] = certainty
        if type(details.get("retryable")) is bool:
            structured["retryable"] = details["retryable"]
        auth = safe_auth_error_details(details)
        if auth is not None:
            structured.update(auth)
        if structured.get("submission_state") == "not_sent":
            structured["remote_outcome"] = "not_sent"
        repair = details.get("input_repair")
        if (
            structured["category"] == "caller"
            and structured.get("submission_state") == "not_sent"
            and isinstance(repair, dict)
        ):
            structured["input_repair"] = repair
            structured["remote_outcome"] = "not_sent"
    key, value = _validated_job_ref(args)
    if key:
        structured[key] = value
    repair = structured.get("input_repair")
    if isinstance(repair, dict):
        missing = [item for item in repair.get("missing", ()) if isinstance(item, str)]
        action = repair.get("action") if isinstance(repair.get("action"), str) else None
        text = f"Remote request was not sent ({stage})."
        if missing:
            text += f" Missing required argument(s): {', '.join(missing)}."
        if action:
            text += f" {action}"
    elif structured.get("hint"):
        text = (
            f"Remote request was not sent ({stage})."
            if structured.get("submission_state") == "not_sent"
            else f"Remote outcome unconfirmed ({stage}); inspect the original operation before repeating it."
        )
        text += " " + structured["hint"]
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


def _observed_exit_code(result):
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
            if isinstance(nested, dict) and isinstance(nested.get("exit_code"), int) and not isinstance(nested.get("exit_code"), bool):
                return nested["exit_code"]
    return None


def _completed_command(name, result):
    if name not in _COMMAND_TOOLS or not isinstance(result, dict):
        return False
    error_details = result.get("error_details")
    if result.get("unknown") or (
        isinstance(error_details, dict) and error_details.get("submission_state") == "uncertain"
    ):
        return False
    if (result.get("state") or result.get("status")) not in _TERMINAL_COMMAND_STATES:
        return False
    return _observed_exit_code(result) is not None


def remote_result(name, payload):
    """Preserve native content blocks and a completed nonzero command."""
    result_payload = payload.get("result", payload) if isinstance(payload, dict) else payload
    text = payload.get("text") if isinstance(payload, dict) else None
    blocks = [dict(type="text", text=text if isinstance(text, str) else canonical_json(payload))]
    native = payload.get("content") if isinstance(payload, dict) else None
    if isinstance(native, list):
        blocks.extend(item for item in native if isinstance(item, dict) and isinstance(item.get("type"), str))
    if isinstance(result_payload, dict):
        error_details = result_payload.get("error_details")
        uncertain = (
            result_payload.get("status") == "submission_uncertain"
            or result_payload.get("unknown")
            or (isinstance(error_details, dict) and error_details.get("submission_state") == "uncertain")
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
                or (
                    isinstance(result_payload.get("error_details"), dict)
                    and result_payload["error_details"].get("operation_completed") is True
                )
            )
            if completed_locally:
                outcome = result_payload.get("execution_outcome", outcome)
        is_error = outcome not in {None, "success", "cancelled"}
    else:
        is_error = False
    return dict(content=blocks, structuredContent=result_payload, isError=is_error)
