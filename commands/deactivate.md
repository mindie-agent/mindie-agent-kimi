---
description: Revoke MindIE knowledge authorization for the current Kimi session
---

Call `mindie_entry` once with `op=deactivate` and a fresh `request_nonce`. Never pass a session ID.

This revokes only the current session's knowledge and capture lease across registered domains while keeping saved install-level sharing preferences intact. Deterministic `remote` and `components` tools remain available, and invoking `/mindie-agent` re-enables knowledge for this session at any time.

$ARGUMENTS
