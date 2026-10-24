# mindie-agent-kimi 0.1.2 Release Notes

[中文](https://github.com/mindie-agent/mindie-agent-kimi/blob/v0.1.2/docs/releases/mindie-agent-kimi-0.1.2.md) | **English**

`mindie-agent-kimi 0.1.2` is a maintenance release for Kimi Code (paired with `mindie-agent 0.1.1`, while preserving the existing immutable `0.1.1` plugin tag). It introduces independent formal GitHub Release auto-updates for shared runtime components with pointer-only component cutover and preserves the per-session manual `/mindie-agent` knowledge gate.

## Highlights

- **Independent Shared Component Auto-Update Without Reinstalling Native Definitions**:
  - When no newer plugin Release is needed, the updater discovers formal GitHub Releases directly from official component repositories, selecting the non-downgrading maximum across the currently installed version, the plugin baseline, and the latest formal component Release, and building a side-by-side isolated `uv` generation with a verified `uv.lock`.
  - After passing the model-free runtime probe and confirming the service is idle, component-only updates atomically switch `current.json` while retaining the existing `host-package` for rollback, **without reinstalling native Kimi plugin definitions** (`update_native=False`).
  - When staging a newer plugin Release, component preparation preserves the installed component version floor so a plugin upgrade never downgrades a component that was already updated independently.
- **Exploration Payload Pass-Through & Explicit Session Gate**:
  - Exploration outputs from `mindie-knowledge` pass straight through without host-side rewriting. Background update checks never trigger public knowledge feed sync, grant session knowledge authority, or modify saved user consent; knowledge features remain active only after manually invoking `/mindie-agent` in the current session.

## Runtime Components & Verification Scope

- **Paired Runtime Components**: Targets the formal releases of `mindie-knowledge 0.13.3`, `remote-dev 0.10.6` (`0463a13fce9618ec9a51904869d61acb0cdb8538`), and `mindie-diagnostics 0.5.2` (`801a01b2789aed7f58e971a909205a7d40b61077`). Baseline component revisions and supported Kimi Code versions are recorded in `runtime-requirements.txt` and `harness-compatibility.json` (while side-by-side component candidate environments generate their own `uv.lock` during isolated builds).
- **Verification & Acceptance Boundary**: Passed the existing updater suite (`20 passed`) and the new component-switch tests (`2 passed`). Cross-host end-to-end native new-session acceptance after formal package publication is tracked with the main product release gate.
