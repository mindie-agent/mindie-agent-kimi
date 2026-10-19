---
description: Opt in to public community contribution for authorized project roots
---

Required arguments: --repository owner/repo --account name --project-root /absolute/path --visibility public
Optional: --branch main --fork owner/repo

Call `mindie_entry` once with `op=sharing-enable` and a fresh `request_nonce`. Never pass a session id. Destination comes from this slash command's native arguments, not a replacement you invent. Sharing stays off until this explicit public opt-in.

The unified contribution choice in `mindie-consent.json` governs both public knowledge sharing and sanitized product-fault reporting. Report local config state, background worker state, and external GitHub Issue/PR publication separately; saved consent alone does not prove worker readiness. Legacy knowledge-only choices require one explicit re-confirmation before fault uploads start.

$ARGUMENTS
