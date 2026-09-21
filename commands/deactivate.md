---
description: Revoke MindIE Agent authorization for this native Kimi session
---

Call `mindie_entry` once with `op=deactivate` and a fresh `request_nonce`. Never pass a session id. Attempted capture identities are kept so a later activation does not replay them.
$ARGUMENTS
