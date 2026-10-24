# mindie-agent-kimi 0.1.2 Release Notes

[中文](https://github.com/mindie-agent/mindie-agent-kimi/blob/v0.1.2/docs/releases/mindie-agent-kimi-0.1.2.md) | **English**

`mindie-agent-kimi 0.1.2` is a maintenance release for Kimi Code (paired with `mindie-agent 0.1.1`, while preserving the existing immutable `0.1.1` plugin tag). It introduces independent formal GitHub Release auto-updates for shared runtime components with pointer-only component cutover, adds Kimi `2.3.1` native user-entry support, and preserves the per-session manual `/mindie-agent` knowledge gate.

## Highlights

- **Independent Shared Component Auto-Update Without Reinstalling Native Definitions**:
  - When no newer plugin Release is needed, the updater discovers formal GitHub Releases directly from official component repositories, selecting the non-downgrading maximum across the currently installed version, the plugin baseline, and the latest formal component Release, and building a side-by-side isolated `uv` generation with a verified `uv.lock` (`CHECK_BUDGET = 1200.0s`, `INTERVAL_SECONDS = 300`).
  - After passing the model-free runtime probe and confirming the service is idle, component-only updates atomically switch `current.json` while retaining the existing `host-package` for rollback, **without reinstalling native Kimi plugin definitions** (`update_native=False`).
  - When staging a newer plugin Release, component preparation preserves the installed component version floor so a plugin upgrade never downgrades a component that was already updated independently.
- **Kimi `2.3.1` Compatibility & Current-Turn Native User `/mindie-agent` Entry (`scripts/identity.py`, `harness-compatibility.json`)**:
  - Added Kimi `2.3.1` to `harness-compatibility.json` alongside `2.1.1` and `2.2.0`.
  - When Kimi `2.3.1` retains a user's leading `/mindie-agent` input in `turn.prompt` / `context.append_message` (`kind == "user"`) instead of expanding a Skill, `scripts/identity.py` authorizes `init` from the current turn's native user opening and binds `activation_id="user-entry:<promptId>"`—never deriving a grant from model arguments, mid-sentence mentions, or inherited transcript text.
- **Exploration Payload Pass-Through & Explicit Session Gate**:
  - Exploration outputs from `mindie-knowledge` pass straight through without host-side rewriting. Background update checks never trigger public knowledge feed sync, grant session knowledge authority, or modify saved user consent; knowledge features remain active only after manually invoking `/mindie-agent` in the current session.

## Runtime Components & Verification Scope

- **Paired Runtime Components**: Locks formal releases of `mindie-knowledge 0.13.3` (`38a42f6f778cfa555d8918fdcacd12cee4a181f5`), `remote-dev 0.10.6` (`0463a13fce9618ec9a51904869d61acb0cdb8538`), and `mindie-diagnostics 0.5.2` (`801a01b2789aed7f58e971a909205a7d40b61077`). Baseline component revisions and supported Kimi Code versions (`2.1.1`, `2.2.0`, `2.3.1`) are recorded in `runtime-requirements.txt` and `harness-compatibility.json` (while side-by-side component candidate environments generate their own `uv.lock` during isolated builds).
- **Verification & Acceptance Hierarchy**:
  1. **macOS `arm64` Native Candidate Verification (Kimi `2.2.0` & `2.3.1`)**:
     - On Kimi `2.2.0`, verified that a default fresh session rejects `knowledge_query` before execution without initializing the knowledge service or retrying; manual `/mindie-agent` activation sets `entry_enabled=1`, is inherited across same-session continuation turns, and is not inherited by an independent fresh session; default `remote_job_status` executes through the deterministic remote path (returning a normal caller/not-found error for a missing `job_id` without being blocked by the knowledge gate); independent component updates verified exact alignment with `mindie-knowledge 0.13.3`, `remote-dev 0.10.6`, and `mindie-diagnostics 0.5.2` while preserving the native bundle.
     - On Kimi `2.3.1`, completed isolated native `setup.py` + `NativePluginAPI` installation and full session boundary verification: a real user's current-turn leading `/mindie-agent` prompt with a native `promptId` initializes once (`init`), binds the real native `session_id` (`entry_enabled=1`), keeps the background service `not-requested`, and performs no capture or sharing; same-session continuation (`CONTINUED`) inherits the grant, while both an independent fresh session and a session forked from the authorized session reject knowledge calls without adding any lease record, leaving only the single manually activated session in the SQLite admission store.
  2. **Automated Suites & Multi-OS CI**: Passed the updater suite, component-switch tests, and Kimi `2.3.1` native user-entry identity tests, with cross-OS contracts covered by per-OS CI workflows.
  3. **Post-Publication Local Update Readback**: Final local auto-update readback against published `0.1.2` GitHub Release packages remains pending until the four host `0.1.2` tags are formally published.
