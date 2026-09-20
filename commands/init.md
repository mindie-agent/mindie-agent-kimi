---
description: Initialize MindIE Agent for this native Kimi session, or show first-use choices
---

Call the knowledge MCP tool `mindie_entry` exactly once with `op=init` and a fresh `request_nonce`. Never pass a session id.

Show the tool result to the user. If it includes three choices, present them as the first-use reply:
1. Recommended: contribute public experience (`/mindie-agent:sharing-enable --repository owner/repo --account name --project-root /absolute/path --visibility public`).
2. Read-only knowledge; no contribution.
3. Configure later (sharing stays off).

If the user then replies `read-only` or `later`, call `mindie_entry` with `op=choose`, that `choice`, and a new `request_nonce`. That reply is an ordinary user turn; do not require another slash. The user may also run `/mindie-agent:init read-only` or `/mindie-agent:init later`. There is no automatic yes.
$ARGUMENTS
