---
description: Bind MindIE Agent for this native Kimi session and report missing configuration
---

`/mindie-agent` is the single entry; this command is its alias. Call
`mindie_entry` once with `op=init` and a fresh `request_nonce`. Never pass a
session id. Supplied `contribute owner/repo ACCOUNT` arguments configure the
current project. Reuse approved values and ask only for missing information.
Report `experience`: missing configuration, disabled state, scope mismatch and
faults are not successful setup. The configured loop processes eligible Stop
events automatically; actual completion needs processing receipts.
Do not offer read-only/later product modes. Preserve legacy saved declines
until explicitly changed. `/mindie-agent disabled` explicitly stops capture.

For setup or a GitHub authentication failure, follow [GitHub Sign-In](../skills/mindie-agent/references/github-authentication.md). Reuse existing authentication; offer browser login or enterprise-network token creation/input only when needed.

During installation or first-use setup, present the [user notice](../skills/mindie-agent/references/user-notice.md),
then follow [optional workspace setup](../skills/mindie-agent/references/workspace-setup.md).
Reuse the user's creation and initialization decisions.

The unified contribution choice in `mindie-consent.json` governs both public knowledge sharing and sanitized product-fault reporting. Report local config state, background worker state, and external GitHub Issue/PR publication separately; saved consent alone does not prove worker readiness. Reuse valid saved consent. A legacy knowledge-only `contribute` returning `needs_choice` requires the explicit native contribution entry once; supplied contribution arguments already express that choice, so do not request a second confirmation. Ordinary startup never upgrades consent.

$ARGUMENTS
