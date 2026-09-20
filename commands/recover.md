---
description: Inspect or compact a contribution batch without replaying organizer or capture
---

Optional: --batch <id>. Call `mindie_entry` once with `op=recover` and a fresh `request_nonce`. Never pass a session id. Batch id comes from this slash command's native arguments. Does not rerun the organizer, reset capture cursors, or replay failed model attempts.
$ARGUMENTS
