---
description: Bind MindIE Agent for this native Kimi session (same as /mindie-agent), or show first-use choices
---

`/mindie-agent` is the single entry; this command is its alias. Call the
knowledge MCP tool `mindie_entry` exactly once with `op=init` and a fresh
`request_nonce`. Never pass a session id.

Arguments appended below belong to this invocation. `op=init` already applies
a supplied `read-only` or `later` argument. If its result shows that choice
persisted, display it and finish; an additional `op=choose` call is unnecessary.

Show the tool result to the user. If it includes three choices, present them
as the one-time setup reply:
1. Recommended: contribute public experience for the current project — the
   user names the public repository (`owner/repo`) and account in their
   reply, or runs `/mindie-agent contribute owner/repo ACCOUNT`.
2. Read-only knowledge; no contribution.
3. Configure later (sharing stays off).

If the user then replies `read-only` or `later`, call `mindie_entry` with
`op=choose`, that `choice`, and a new `request_nonce`. That reply is an
ordinary user turn; do not require another slash. The user may also run
`/mindie-agent read-only` or `/mindie-agent later`. There is no automatic
yes. A saved choice persists for this installation and is never re-asked.
$ARGUMENTS
