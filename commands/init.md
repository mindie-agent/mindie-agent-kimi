---
description: Bind MindIE Agent for this native Kimi session and report missing configuration
---

This command is an alias of `/mindie-agent`. Call `mindie_entry` once with `op=init` and a fresh `request_nonce`.

Supplied `contribute owner/repo ACCOUNT` arguments configure community contribution for the current project, and `/mindie-agent disabled` disables capture. Reuse already-approved values and ask only for missing settings.

The unified contribution setting in `mindie-consent.json` governs both public knowledge sharing and sanitized product-fault reporting. Reuse valid saved choices (`contribute`, `disabled`, `read-only`, `later`); if a legacy knowledge-only `contribute` returns `needs_choice`, resolve it once via `/mindie-agent contribute owner/repo ACCOUNT`. Report local config state, background worker status, and external GitHub Issue/PR creation separately.

For GitHub authentication setup or troubleshooting, follow [GitHub Sign-In](../skills/mindie-agent/references/github-authentication.md). During initial installation or first-use setup, present [用户须知](../skills/mindie-agent/references/user-notice.md) and follow [Workspace Setup](../skills/mindie-agent/references/workspace-setup.md).

$ARGUMENTS
