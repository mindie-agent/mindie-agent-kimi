---
name: mindie-agent
description: Manually activate MindIE Agent knowledge and optional community sharing for this Kimi session when the user explicitly invokes /mindie-agent:init for a vLLM-Ascend task.
disableModelInvocation: false
---

# MindIE Agent

Use `/mindie-agent:init` for the current native Kimi task. Discussing the
plugin does not activate it. Slash commands submit origin.kind=plugin_command;
a native TurnStarted hook binds that origin from this session's wire. Do not
pass a session id. `${KIMI_SESSION_ID}` in this text is not a capability.

Sharing is OFF unless `/mindie-agent:sharing-enable` is used. While off there
is no Stop capture or organizer.

Every knowledge and remote MCP call MUST include a fresh `request_nonce`.
Never pass `session_id`. Remote-dev does not require knowledge activation.

Recovery uses core `contribution-inspect`, `contribution-reconcile`, and
`contribution-compact` with `--batch`. Details:
[activation lifecycle](references/activation-lifecycle.md),
[domain tooling](references/domain-skills.md).
