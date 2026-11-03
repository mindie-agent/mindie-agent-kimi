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
            "Native MindIE entry for Kimi Code (requires a fresh request_nonce). "
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
            required=["op", "request_nonce"],
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
            required=["request_nonce"],
            additionalProperties=False,
        ),
    ),
    dict(
        name="knowledge_distill",
        description=(
            "Distill caller-selected, authorized evidence into graded knowledge units "
            "at task_state='verified_milestone' or 'completed' (requires an active "
            "/mindie-agent session and a fresh request_nonce). Pass related_refs to "
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
            required=["inputs", "task_state", "request_nonce"],
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
            required=["request_nonce"],
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
            required=["ref", "request_nonce"],
            additionalProperties=False,
        ),
    ),
]
