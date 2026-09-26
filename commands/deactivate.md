---
description: Unbind MindIE Agent from this native Kimi session (install-level choice is untouched)
---

Call `mindie_entry` once with `op=deactivate` and a fresh `request_nonce`.
Never pass a session id. This removes only this task's internal binding; the
saved install-level sharing/reporting choices are unchanged, attempted
capture identities are kept so a later binding does not replay them, and
invoking `/mindie-agent` binds the task again without any setup.
$ARGUMENTS
