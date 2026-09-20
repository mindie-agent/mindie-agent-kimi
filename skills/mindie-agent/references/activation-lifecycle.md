# Activation lifecycle (Kimi)

- Native identity is `session_id` on hook stdin. Resume keeps the same id; `/fork` creates a new id (`state.forkedFrom`) that does not inherit the lease.
- `/mindie-agent:init` is a plugin_command origin. UserPromptSubmit does not run for that origin.
- There is no `sessionStart` Skill.
- Sharing is a separate file (`mindie-community-config/1`).
- Stop capture is gated: active lease, sharing on, cwd in scope, claim first=True. Stop never starts the knowledge service.
- MCP: 0.42.0 tools/call has no session `_meta`. PreToolUse binds `request_nonce` to exact tool + arguments + `tool_call_id`.
- Recovery: `contribution-inspect` / `contribution-reconcile` / `contribution-compact`.
