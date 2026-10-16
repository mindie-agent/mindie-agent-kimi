# Organizer Failure Classification & Process Groups

<a href="organizer-diagnostics.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

When community experience sharing is enabled, `mindie-agent-kimi` invokes an isolated Kimi CLI organizer to extract block titles and summaries. This document describes the static exit-code classification and process-group lifecycle guarantees for the organizer.

---

## 1. Isolated Execution & Static Error Categories

- **Isolated Profile**: The organizer runs inside a temporary profile with no plugins, skills, hooks, or MCP servers loaded, and cleans up its temporary directory upon completion.
- **Static Exit-Code Mapping**: Configuration errors, invalid inputs, native execution failures, schema-invalid outputs, output-size overflows, and deadlines (120s inner / 125s outer deadline) map to static shared failure categories.
- **Zero Secret Leakage**: The shared Core records only the static failure category, exit code, and elapsed seconds—never copying raw provider stderr, prompts, or environment variables into persisted diagnostics. Failed inputs are never automatically replayed.

---

## 2. Unified Process-Group Cleanup

- **Shared Process-Group Ownership**: The organizer script and its spawned native Kimi child process remain in the Core-owned process group so that normal completion, timeout, or cancellation reliably closes the entire process tree without leaving orphan processes behind.
- **Verification Scope**: Verified on macOS across all 7 process termination paths (configuration, input, native failure, invalid result, output limit, 120s deadline, and success) as well as isolated K3 organizer calls, confirming sub-second cancellation cleanup and zero lingering child processes.
