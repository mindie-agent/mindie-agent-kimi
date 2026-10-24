# Failure Diagnostics & Local Cancellation

<a href="failure-diagnostics.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

Following Principle 9 of the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md) (*observable, diagnosable tools; never hide errors*), `mindie-agent-kimi` provides read-only status inspection, deterministic local process cleanup on cancellation, and preserved remote job references.

---

## 1. Read-Only Status Inspection (`/mindie-agent:status`)

- **What It Reports**: Factual control-plane state (`status_facts`: `configured`, `knowledge_enabled`, `this_session` manual-entry lease state, `diagnostics` covering current knowledge configuration, service/storage/admission health, and the latest 5 captures, summaries, and contribution batches associated with the bound task, plus `reporting` status) without onboarding prompt fields (`first_use`, `next`, `recovery`) and without requiring prior `/mindie-agent` session activation.
- **Strictly Read-Only**: Calling `status` never initializes databases, starts models or background services, binds a task, resets failure counters, or replays consumed input. Uncertain external PR publications must be reconciled before any explicit `recover` retry.
- **Sanitized Diagnostics & Corrupt Config Detection**: Failure projections omit raw provider stderr, transcript text, and tokens. If an existing adapter configuration file is malformed, `status` reports an explicit diagnostic error rather than presenting first-time onboarding choices.
- **Independent Remote Execution**: Native shell/SSH and `remote-dev` remain fully usable even if the local knowledge service is unavailable.

---

## 2. Local Cancellation vs. Remote Ascend Jobs

- **Responsive Stdio Frontend**: The MCP stdio frontend stays responsive to `ping`, cancellation, and `EOF` while tool requests execute.
- **Local Process Cleanup**: Cancelling a request or closing stdin (`EOF`) terminates only the local helper subprocesses owned by that request.
- **Preserved Remote Job Identity**: Cancelling a local observation or polling call does **not** stop a remote compilation or test job on the Ascend server. The response preserves the original remote job reference and delivery certainty so you can explicitly inspect or stop the remote job before submitting a new one. Remote poll requests wait up to 30 seconds per call without altering the remote workload's timeout.
