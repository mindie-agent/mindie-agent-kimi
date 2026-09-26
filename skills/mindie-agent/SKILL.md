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

First use presents the one-time choices (contribute / read-only / later)
only if no choice was ever saved. A later `read-only` or `later` reply is an
ordinary user turn: call `mindie_entry` `op=choose`. Contribution requires
`/mindie-agent:sharing-enable` with repository, account, project root, and
`--visibility public`. There is no automatic yes. A saved choice persists
across new sessions, forks, restarts, upgrades and failures — it is never
re-asked, and failure counts never revoke it.

Sharing is OFF unless that explicit enable succeeds. While off there is no
Stop capture or organizer. Binding is automatic and internal: there is no
activate/lease/recover step for the user.

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
