# mindie-agent-kimi (`v0.0.2` Agent-Native Guide)

Native Kimi Code plugin adapter for MindIE Agent (`kimi.plugin.json`, `commands/`, `skills/`, `scripts/`).
Bridges native Kimi Hook/MCP/Skill surfaces to shared first-party runtime components (`knowledge`, `remote-dev`, `diagnostics`).

## 1. Script Module Navigation (`scripts/`)

| Module | Responsibility | Key Entrypoints / State |
| :--- | :--- | :--- |
| `mindie_launch.py` | Stable front launcher outside committed generations (`hook`, `mcp`, `updater`) | `<state_dir>/update/current.json`, `operation.lock` |
| `bridge.py` | Native Kimi `PreToolUse` (`pretool`) and `Stop` (`stop`) hook bridge | `handle_pretool()`, `handle_stop()`, `capture_hook` |
| `mcp_server.py` | JSON-RPC 2.0 MCP server for `knowledge` (requires `Admission.knowledge_lease(session)`) and `remote` surfaces (available without `/mindie-agent`) | `handle()`, `serve()`, `serve_once()` |
| `identity.py` | Session directory lookup, turn-origin verification, and one-shot nonce binds | `require_current_entry()`, `publish_nonce_bind()`, `claim_nonce()`, `mcp-binds.sqlite3` |
| `entry.py` & `entry_state.py` | `/mindie-agent` unified entry and slash command operations (`init`, `choose`, `status`, `recover`); `op_status` returns factual `status_facts` without command-origin gating or onboarding prompts | `dispatch()`, `status_facts()`, `status_payload()`, `consume_activation_id()` |
| `admission.py` & `sharing.py` | Task admission lease helpers (`knowledge_lease`, `entry_enabled=1`) and community sharing status/configuration | `admission.sqlite3`, `mindie-community.json` |
| `consent.py` | Profile-shared consent through `mindie_knowledge.consent_store` and generation-stranded state convergence | `load()`, `record_choice()`, `adopt_community_settings()` |
| `transcript.py` | Bounded Kimi `wire.jsonl` reader with fork-boundary (`state.createdAt`) filtering | `identify()`, `read_material()` |
| `organizer.py` | Isolated `kimi -p` summary worker (`--output-format text`, thinking off, zero tools/hooks) | `identity()`, `run()`, `prepare_isolated_home()` |
| `knowledge_service.py` & `service_handoff.py` | Background knowledge service lifecycle and authenticated updater `stop_if_idle`/`restore` | `ensure_service()`, `stop_if_idle()` |
| `updater.py` & `genstate.py` | Atomic generation staging, native plugin install/rollback, and scheduler control (never starts knowledge service or feed sync during passive checks) | `check()`, `switch()`, `check.lock`, `operation.lock` |
| `setup.py` & `install_kimi_plugin.py` | Pinned runtime bootstrap (`bootstrap-venv`) and native `kimi web` plugin API installer | `build_bootstrap_runtime()`, `verify_install_report()` |
| `bounded.py` & `with_runtime.py` | Bounded subprocess runner with process-tree cleanup and runtime `sys.path` injector | `bounded.run()`, `classify_transport_text()` |
| `paths.py`, `capture_config.py`, `community_config.py` | Canonical config/state path resolution and core engine config builders | `kimi.json`, `kimi.engine.json` |
| `diagnostic_support.py` | Shared `mindie_diagnostics.integration` fault recording and opt-in reporting; reports `logging_failed` when unavailable | `failure()`, `attach()`, `diagnostics.json` |

## 2. Host Protocols & Core State Invariants

- **PreToolUse Nonce Binding**: Kimi MCP `tools/call` carries only `name` and `arguments`. Every MindIE MCP tool requires a fresh `request_nonce` bound by `PreToolUse` (`bridge.py pretool` -> `mcp-binds.sqlite3`) and claimed atomically (`BEGIN IMMEDIATE`) before execution. Replayed, expired (`> 120s`), or mismatched nonces fail closed.
- **Manual `/mindie-agent` Knowledge Gate vs. Default-On Deterministic Tools**: Consuming a `request_nonce` proves native session identity, not knowledge activation. Deterministic `remote-dev` tools and factual `mindie_entry(op="status")` execute with the consumed `request_nonce` alone, whereas all `knowledge_*` MCP tools (`knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, `knowledge_sync`) and `Stop` transcript capture require an active manual-entry lease (`Admission.knowledge_lease(session)` / `lease.get("entry_enabled") == 1`) granted by `/mindie-agent` in the current session.
- **Stop Hook Notification**: `bridge.py stop` never blocks the host and never fabricates a native turn ID; it checks `lease.get("entry_enabled")` first (returning `{}` for unactivated sessions) before passing a thin notification (`identity_kind="notification"`, session lease token, and `wire.jsonl` path) to `mindie_knowledge.loop.cli.capture_hook`.
- **Atomic Generation Pointer**: `current.json` (`{generation, python, adapter_config, sha}`) is read under a shared `operation.lock` for every Hook/MCP call and flipped under an exclusive `operation.lock` only after `stop_if_idle` and verified native plugin readback.

## 3. Timeout Budgets & `v0.0.2` Engineering Iron Rules

- **Explicit Timeout Hierarchy**:
  - `HOOK_TOTAL = 10.0s` (`HOOK_LOCK_BUDGET = 2.0s`), native host hook timeout `15s`, `CALL_LOCK_BUDGET = 5.0s`, `CALL_CHILD_BUDGET = 60.0s` (`180s` for explicit contribution activation/configuration, including shared reporter startup), `MAX_YIELD_TIME_MS = 30000` (30s).
  - `BOOTSTRAP_TOTAL = 540.0s`, summary native timeout at most `300s` and bounded by `summary_budget.native_timeout()` with `5s` reserved for cleanup, `CHECK_BUDGET = 240.0s` (with `RESERVE = 53.0s`: `ROLLBACK_BUDGET = 45.0s` + `HANDOFF_BUDGET = 8.0s`).
- **Four `v0.0.2` Iron Rules**:
  1. **Lightweight SQLite & File I/O**: Use `busy_timeout=5000` and `os.replace()` atomic writes; never run recursive parent symlink checks, `O_NOFOLLOW`/`S_ISREG` descriptor dances, or per-read `chmod` ceremonies.
  2. **Honest `unknown` Boundary**: Reserve `outcome_unknown` / `unconfirmed` strictly for interrupted paid model invocations or external remote writes; never brick local state with permanent one-way fault lockouts.
  3. **No Defensive Bloat**: Keep adapter scripts concise and direct; do not duplicate core engine logic or invent `ctypes` ACL/pipe ceremonies.
  4. **No Filler or Paranoid Tests**: Never write `chmod(0)`/`icacls` permission games, FIFO/symlink traps, or prose-grep tests.

## 4. Fast Verification Commands

```bash
# Run the full test suite with the pinned verification environment:
/Volumes/data/code/mindie-release-20261014/codex-interrupt-final-env-20261016/bin/python -m pytest
```
