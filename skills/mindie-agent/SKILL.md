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
   - Once community contribution is enabled for the approved project scope, the configured loop processes eligible `Stop` events **automatically in the background**—users never need to ask to save experience at the end of a session, and one-time setup is never re-asked.
   - If `experience` in the `mindie_entry` response reports incomplete configuration, reuse already-approved values, ask only for the missing public `owner/repo` and GitHub account, and call `mindie_entry` with `op=choose`, `choice=contribute`, and those exact values.

## Progressive Context Disclosure (Read Only on Demand)

Do **not** load all reference files during ordinary troubleshooting. Read the specific reference file **only** when the current turn requires it:

- **Knowledge & Remote MCP tool usage (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `remote_*`)**: read [Domain Tooling](references/domain-skills.md).
- **First-time onboarding or setting up a local `vllm` + `vllm-ascend` multi-worktree workspace**: present [用户须知](references/user-notice.md) and follow [Workspace Setup](references/workspace-setup.md) (offer during setup, not ordinary task execution).
- **Preference changes, disabled/declined profile rules, background worker recovery, or hook lifecycle details**: read [Activation Lifecycle](references/activation-lifecycle.md).
- **Sharing scope, local redaction boundaries, or public Git history questions**: read [用户须知](references/user-notice.md).

