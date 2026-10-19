---
description: Turn community contribution off (cancels pending capture; no backfill)
---

Call `mindie_entry` once with `op=sharing-disable` and a fresh `request_nonce`. Never pass a session id.

The unified contribution choice in `mindie-consent.json` governs both public knowledge sharing and sanitized product-fault reporting. Disabling stops subsequent outgoing sends for both paths; report local config state and background worker state separately.

$ARGUMENTS
