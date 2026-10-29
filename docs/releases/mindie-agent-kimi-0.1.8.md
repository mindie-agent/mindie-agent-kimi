# mindie-agent-kimi 0.1.8

`mindie-agent-kimi 0.1.8` (paired with `mindie-agent 0.1.3`) completes the native three-server MCP registration and component CLI tool surface (`knowledge`, `remote`, and `components`) for Kimi Code alongside the persistent SSH authentication integration from `remote-dev 0.11.1`.

## 1. Key Changes & Behavior

### 1. Three-Server Native MCP Registration & `components` Surface (PR `#53`)

- `kimi.plugin.json` registers three stdio MCP servers—`knowledge`, `remote`, and `components`—with `toolTimeoutMs=660000` (`11 minutes`) on `remote` and `components`, preserving `outer_timeout >= inner_timeout (600s) + cleanup_grace`.
- `scripts/setup.py` reconciles `~/.kimi/mcp.json` across both new installations and active-generation upgrades so `mindie-agent.knowledge`, `mindie-agent.remote`, and `mindie-agent.components` are registered in Kimi Code without disturbing unrelated user MCP servers.
- The `components` MCP server (`scripts/component_tools.py` & `scripts/mcp_server.py`) exposes 5 always-on tools without requiring `/mindie-agent` session activation:
  - `diagnostics_status` and `diagnostics_bundle` (backed directly by bundled `mindie-diagnostics 0.5.4`);
  - `coordinator`, `profiler`, and `sim` CLI bridges (discovering optional `mindie-coordinator`, `mindie-profiler`, and `mindie-sim` commands in the configured Python environment, `PATH`, or `component_commands`, with bounded output budgets and process-group termination on timeout).
- The `knowledge` MCP server remains strictly gated by explicit per-session `/mindie-agent` nonce binding.

### 2. Active Runtime & Test Component Pins

The immutable `0.1.2` controller bootstrap trio in `runtime-requirements.txt` remains `mindie-knowledge 0.13.3`, `remote-dev 0.10.6`, and `mindie-diagnostics 0.5.2`, while `runtime-active-overrides.txt` and `test-requirements.txt` pin the active runtime and test component versions:

| Component | Role | Version / Tag | Source Commit |
| --- | --- | --- | --- |
| `remote-dev` | Active runtime override | `0.11.1` / `v0.11.1` | `2a750ec8c74d7f053410d48dd820aeb542e7d3b1` |
| `mindie-diagnostics` | Active runtime override | `0.5.4` / `v0.5.4` | `5ae643e19223f2181ecdbaa3edaf464137c4805e` |
| `mindie-knowledge` | Controller runtime pin | `0.13.3` / `v0.13.3` | `38a42f6f778cfa555d8918fdcacd12cee4a181f5` |
| `mindie-profiler` | `test-requirements.txt` & optional CLI pin | `0.5.1` / `v0.5.1` | `25c789cd42389c893234c5db20eca67e63b8878a` |
| `mindie-sim` | `test-requirements.txt` & optional CLI pin | `0.7.1` / `v0.7.1` | `15f93f3f0247b2653d0e9441eae56959e4d16d4e` |
| `mindie-coordinator` | `test-requirements.txt` & optional CLI pin | `0.7.0` / `v0.7.0` | `e4a86c972a99c442c78ad7f7ba627af7da5ef7f2` |

## 2. Verification & Evidence Scope

- Verified via the multi-platform CI matrix (Ubuntu, macOS, Windows), `tests/preflight.py`, `scripts/release_metadata.py`, and MCP stdio / migration tests (`tests/test_mcp.py`, `tests/test_component_tools.py`, `tests/test_update_migration.py`).
- Formal tag release workflows attach `mindie-plugin-release.json`, `mindie-agent-kimi-0.1.8.tar.gz`, `benchmark-results.json`, `benchmark-results.md`, and `SHA256SUMS` built from the exact tagged commit.
