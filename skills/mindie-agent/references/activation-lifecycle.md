# Activation lifecycle (Kimi)

- Native identity is `session_id` on hook stdin. Resume keeps the same id; `/fork` creates a new id (`state.forkedFrom`) that does not inherit the binding. A fork that invokes `/mindie-agent` gets its own independent binding with its own capture boundary — never the parent's capability, job ownership or collectable history.
- `/mindie-agent` (and its `:init` alias) is a plugin_command origin. UserPromptSubmit does not run for that origin.
- Slash control is one `mindie_entry` MCP call bound by PreToolUse nonce. The current turn-opening origin must be this plugin command. `activationId` is consumed once. Older matching commands are ignored. Caller task IDs are rejected.
- A later `read-only`/`later` reply is an ordinary user turn (`op=choose`), or `/mindie-agent read-only|later`.
- There is no `sessionStart` Skill. TurnStarted only records `turn_id`; it does not replay commands.
- Sharing is a separate install-level file (`mindie-community-config/1`), the single persistent choice. Enable destination is native `commandArgs`. The saved choice never expires: restarts, upgrades, forks and failure counts never revoke it and never re-ask. Explicitly disabled stays disabled.
- The per-task lease is an internal binding established by the entry — not a consent step. It has no failure pause: ordinary failures never require deactivate/reactivate. Explicit `deactivate` only unbinds the task.
- Stop capture is gated: live binding, sharing on, cwd in scope, claim first=True. Only a bound task with sharing ON durably hands off material and may wake the capture service; sharing OFF does not start it. A knowledge query restoring its own service is a separate read path.
- MCP: 0.42.0 tools/call has no session `_meta`. PreToolUse binds `request_nonce` to exact tool + arguments + `tool_call_id`.
- Sharing status shows a problem. A transient local or network failure is recovered by the existing worker — a deadline-interrupted region gets one bounded background recovery, not a contribution batch command or another model turn. Authentication, trust, rejected content, or invalid configuration can need an explicit user or operator action. Batch inspection is optional troubleshooting, not a recovery step.
