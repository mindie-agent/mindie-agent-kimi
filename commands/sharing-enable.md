---
description: Opt in to public community contribution for authorized project roots
---

Required arguments: --repository owner/repo --account name --project-root /absolute/path --visibility public
Optional: --branch main --fork owner/repo

Call `mindie_entry` once with `op=sharing-enable` and a fresh `request_nonce`. Never pass a session id. Destination comes from this slash command's native arguments, not a replacement you invent. Sharing stays off until this explicit public opt-in.
$ARGUMENTS
