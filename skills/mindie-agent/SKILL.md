---
name: mindie-agent
description: The single MindIE Agent entry. Invoke /mindie-agent in a vLLM-Ascend task to bind it internally and use knowledge, skills, optional community sharing, and remote-dev. Once opted in, experience capture runs automatically at session end without per-task prompts.
disableModelInvocation: false
---

# MindIE Agent (Unified Entry)

This skill is the **only** entry a user ever needs to invoke (`/mindie-agent`).
Meanwhile, `remote-dev` MCP tools are always available on demand in normal conversation without invoking `/mindie-agent`.

1. **Bind & Initialize on Invocation**:
   - When `/mindie-agent` is invoked in the current native Kimi task, call `mindie_entry` **exactly once** with `op=init` and a fresh `request_nonce`. Never pass or fabricate a session ID (`${KIMI_SESSION_ID}` in text is not a capability).
   - Every subsequent knowledge and remote MCP call MUST also include a fresh `request_nonce`.
2. **Automatic Experience Loop by Default**:
   - Once the unified contribution choice is enabled for the approved project scope, eligible `Stop` events run **automatically in the background**. Reuse a valid saved choice; no per-task permission or end-of-session save request is needed.
   - Inspect `first_use` and `reporting_activation` from the entry response. Preserve saved `disabled`, `later`, and `read-only` choices without prompting. If no choice exists, present the [contribution notice](references/user-notice.md) once and accept `contribute` or `disabled`. For a first conversational contribution choice, call `op=choose` only with the public `owner/repo` and GitHub account the user supplied in that reply.
   - If saved legacy `contribute` returns `needs_choice`, explain once that contribution now includes sanitized product-fault reporting. The supported migration entry is `/mindie-agent contribute owner/repo ACCOUNT`; its explicit arguments are the confirmation, so do not ask again. Never turn incomplete configuration or an old knowledge-only approval into a new opt-in. Reuse known destination values and ask only for missing ones.

## Progressive Context Disclosure (Read Only on Demand)

Do **not** load all reference files during ordinary troubleshooting. Read the specific reference file **only** when the current turn requires it:

- **Knowledge & Remote MCP tool usage (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `remote_*`)**: read [Domain Tooling](references/domain-skills.md).
- **First-time onboarding or setting up a local `vllm` + `vllm-ascend` multi-worktree workspace**: present [用户须知](references/user-notice.md) and follow [Workspace Setup](references/workspace-setup.md) (offer during setup, not ordinary task execution).
- **Preference changes, disabled/declined profile rules, background worker recovery, or hook lifecycle details**: read [Activation Lifecycle](references/activation-lifecycle.md).
- **Sharing scope, local redaction boundaries, or public Git history questions**: read [用户须知](references/user-notice.md).

