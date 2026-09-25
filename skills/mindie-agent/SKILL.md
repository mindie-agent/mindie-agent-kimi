---
name: mindie-agent
description: Manually activate MindIE Agent knowledge and optional community sharing for this Kimi session when the user explicitly invokes /mindie-agent:init for a vLLM-Ascend task.
disableModelInvocation: false
---

# MindIE Agent

Use `/mindie-agent:init` for the current native Kimi task. Discussing the
plugin does not activate it. Slash commands submit origin.kind=plugin_command.

For every `/mindie-agent:*` command, call `mindie_entry` once with a fresh
`request_nonce`. Never pass a session id. `${KIMI_SESSION_ID}` in this text
is not a capability.

First use presents three choices. A later `read-only` or `later` reply is an
ordinary user turn: call `mindie_entry` `op=choose`. Contribution requires
`/mindie-agent:sharing-enable` with repository, account, project root, and
`--visibility public`. There is no automatic yes.

Sharing is OFF unless that explicit enable succeeds. While off there is no
Stop capture or organizer.

Every knowledge and remote MCP call MUST include a fresh `request_nonce`.
Remote-dev does not require knowledge activation.

A transient local or network sharing failure is recovered by the existing
worker. Do not run a contribution command or another model turn for it.
Sharing status is how a problem is seen. Authentication, trust, rejected
content, or invalid configuration can need an explicit user or operator
action. Batch inspection is optional troubleshooting, not an activation step.
Details: [activation lifecycle](references/activation-lifecycle.md),
[domain tooling](references/domain-skills.md).
