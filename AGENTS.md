# mindie-agent-kimi

Native Kimi Code plugin adapter for MindIE Agent (`kimi.plugin.json`, `commands/`, `skills/`, `scripts/`).
Provides the `/mindie-agent` entry command/skill, native lifecycle hooks, and MCP wiring to shared first-party runtime components (`knowledge`, `remote-dev`, `diagnostics`).

## 1. Inner-Loop Development Discipline (Always Active)

- **Thin Native Adapter Boundary**: Keep this repository a native Kimi Code plugin—no duplicated knowledge engine, publication store, or legacy VAWS path. Preserve unrelated user `~/.kimi` configuration.
- **Fast Iteration (Principle 1)**:
  - Edit source directly in this checkout and run **only targeted tests** (`python -m unittest tests.test_<module> -q`, stdlib unittest, no pytest/uv in this repo) during development. Run the full suite (preflight, runtime contract, tests/native, full discover in both locale modes) once before final delivery or PR submission.
  - Do **not** create duplicate shadow/snapshot directories, build intermediate wheels, or write `readback.json`/`restoration.json` ceremony files during routine debugging.
- **Error Visibility & `unknown` Safety (Principle 9)**:
  - Keep errors visible to the caller; distinguish `completed`, `failed`, and `unknown` external effects. Never silently swallow failures or retry an uncertain paid model/remote write.

## 2. Progressive Disclosure Map (Read Only When Triggered)

- **Framework Stability & Service Continuity** → [`docs/framework-stability.md`](docs/framework-stability.md) & [`docs/service-continuity.md`](docs/service-continuity.md)
- **Update & Windows Launch Contract** → [`docs/update.md`](docs/update.md) & [`docs/windows-launch-contract.md`](docs/windows-launch-contract.md)
- **Failure Diagnostics & Test Boundaries** → [`docs/failure-diagnostics.md`](docs/failure-diagnostics.md) & [`docs/test-boundaries.md`](docs/test-boundaries.md)
- **Gate — Submitting a Development PR**:
  - Only when preparing a PR, review the final diff against [MindIE Agent Design Principles (Pre-PR Review)](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md#三开发-pr-提交前自查清单pre-pr-review) and include the concise review summary in the PR description.
- **Gate — Publishing a Plugin Release**:
  - Only when publishing a formal Release, verify locked first-party component Releases per [Paired Release Checklist](https://github.com/mindie-agent/mindie-agent/blob/main/docs/release-compatibility-and-update.md#组件与插件配套发布).
