"""[Role] Declarative native MCP schemas for manually enabled knowledge tools.

[Invariants]
- Schemas describe the existing knowledge/entry contract; dispatch owns gates.
- Request nonces carry native identity; tool arguments never grant authority.

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
            "Use when a missing contract, mechanism or method could change the next action. "
            "Skip retrieval when code and current evidence support a fix and verification; "
            "probe missing environment facts first. "
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
            "Read only knowledge needed for the next action; an exact returned ref needs no new query. "
            "For graded units, prefer refs even for one unit, with observed conditions, fully visible "
            "known_refs and an explicit token_budget. Copy revision references exactly. Stop reading "
            "once the next action and required premises are clear; new evidence can reopen retrieval. "
            "Shared prerequisites expand once in requires; primary units "
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
