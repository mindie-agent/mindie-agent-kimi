# Activation lifecycle (Kimi)

- Native identity is `session_id` on hook stdin. Resume keeps the same id; `/fork` creates a new id (`state.forkedFrom`) that does not inherit the lease.
- `/mindie-agent:init` is a plugin_command origin. UserPromptSubmit does not run for that origin.
- Slash control is one `mindie_entry` MCP call bound by PreToolUse nonce. The current turn-opening origin must be this plugin command. `activationId` is consumed once. Older matching commands are ignored. Caller task IDs are rejected.
- A later `read-only`/`later` reply is an ordinary user turn (`op=choose`), or `/mindie-agent:init read-only|later`.
- There is no `sessionStart` Skill. TurnStarted only records `turn_id`; it does not replay commands.
- Sharing is a separate file (`mindie-community-config/1`). Enable destination is native `commandArgs`.
- Stop capture is gated: active lease, sharing on, cwd in scope, claim first=True. Only an activated task with sharing ON durably hands off material and may wake the capture service; sharing OFF does not start it. A knowledge query restoring its own service is a separate read path.
- MCP: 0.42.0 tools/call has no session `_meta`. PreToolUse binds `request_nonce` to exact tool + arguments + `tool_call_id`.
- Sharing status shows a problem. A transient local or network failure is recovered by the existing worker, not by a contribution batch command or another model turn. Authentication, trust, rejected content, or invalid configuration can need an explicit user or operator action. Batch inspection is optional troubleshooting, not an activation step.
