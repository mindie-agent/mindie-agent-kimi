---
name: mindie-agent
description: The single MindIE Agent entry. Invoke /mindie-agent once in a vLLM-Ascend task to bind it internally and use knowledge, optional community sharing, and remote-dev. One-time setup persists for the installation and is never re-asked.
disableModelInvocation: false
---

# MindIE Agent

This skill is the only entry a user needs. Invoking `/mindie-agent` in the
current native Kimi task binds the task internally (reusing the saved
install-level choice), ensures the background service, and returns the
current state. Discussing the plugin does not bind anything.

For this invocation, call `mindie_entry` exactly once with `op=init` and a
fresh `request_nonce`. Never pass a session id. `${KIMI_SESSION_ID}` in this
text is not a capability.

If `experience` reports incomplete configuration, reuse already approved
values and ask only for the missing public repository and account. Call
`mindie_entry` with `op=choose`, `choice=contribute` and those exact values from
the user's reply; the native current project is the scope. Alternatively,
`/mindie-agent contribute owner/repo ACCOUNT` configures it directly.
Configuration then prepares capture for the existing task automatically.

Explicitly disabled and legacy declined profiles remain disabled until the
user changes them; do not offer read-only/later product modes. Missing
configuration, out-of-scope tasks and component faults are not successful
setup. Preserve native binding across failures; no activate/recover step is
required. The configured loop processes eligible Stop events automatically.
Inspect capture and contribution receipts before claiming it has completed.
Fault reporting remains a separate optional setting; it never gates this loop.

Every knowledge and remote MCP call MUST include a fresh `request_nonce`.
Remote-dev does not require the entry.

A transient local or network failure is recovered by the existing background
worker — including one bounded retry of a deadline-interrupted region. Do not
run a contribution command or another model turn for it. Sharing status is
how a problem is seen. Authentication, trust, rejected content, or invalid
configuration can need an explicit user or operator action. Batch inspection
(`/mindie-agent:recover`) is optional troubleshooting, not a recovery step.
Details: [activation lifecycle](references/activation-lifecycle.md),
[domain tooling](references/domain-skills.md).
