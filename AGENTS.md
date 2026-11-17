# MindIE Agent for Kimi Code (`mindie-agent/mindie-agent-kimi`) — Agent Operating Rules

This repository owns the thin Kimi Code host plugin (`kimi.plugin.json`), Kimi native wire/hook/transcript adaptation, and live generation handoff built on the shared [`mindie-harness`](https://github.com/mindie-agent/harness) (`API_VERSION = 1`) substrate.

## 1. Core Architectural Invariants (`host-plugin`)

1. **Thin Host Boundary & `0.1.26 -> 0.1.27` Transition Compatibility (`HOST-UPGRADE`)**:
   - Delegate process supervision, Windows Job Objects, PE `Subsystem == 2` verification, scheduled wrapper generation, atomic writes, and RSI utilities (`rsi_loop.py`, `rsi_explore.py`, `experiment_ledger.py`) to `mindie_harness` and bundle the deterministic cold seed (`assets/mindie-harness-seed.pyz` + `assets/seed-lock.json`).
   - Preserve `< 30`-line `mindie_harness`-delegating transition shims at all paths copied or probed by deployed `0.1.26` `updater_staging.py` (`scripts/mindie_launch.py`, `scripts/bounded.py`, `scripts/diagnostic_support.py`, `scripts/paths.py`, `scripts/launch_budget.py`, `scripts/transcript.py`, `scripts/capture_config.py`, `runtime-requirements.txt`, `runtime/active-overrides.txt`) and preserve `update/current.json` (`{generation, python, adapter_config, sha}`).
   - Keep core Python modules `< 800` lines with a top-30-line `[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]` docstring header, and allow legitimate symlinks (`AD-07` — no `O_NOFOLLOW` hostility on config/repo reads).

2. **Windows 11 Zero-Popup & Scheduled Task Migration (`HOST-NATIVE`)**:
   - Never pass `CREATE_NEW_CONSOLE` or `DETACHED_PROCESS` in any Python script or generated `.pyw` wrapper (`CREATE_NO_WINDOW = 0x08000000` only).
   - Scheduled tasks (`MindIEKimiUpdate` / `agent.mindie.kimi-update.plist`) must verify PE `Subsystem == 2` (`pythonw.exe`), migrate task actions before retiring old generations, and protect OS-scheduled executables (`discover_scheduled_executables`) during `Active + Rollback (<= 2)` storage GC.

3. **Single `/mindie-agent` Entry, `P2.7` Zero-Binding Deterministic MCP & `P2.1` Knowledge Gate (`HOST-WIRE`, `HOST-ADMISSION`)**:
   - Expose a single `/mindie-agent` entry skill (`kimi.plugin.json` carries `"skills": "./skills/"` without duplicate `commands/*.md` slash clutter).
   - Keep `request_nonce` strictly optional on deterministic `remote` and `components` MCP `inputSchema.required` (`P2.7`), share `state_dir() / "remote"` across parent and subagent sessions so subagents can inspect/cancel parent `job_id`s (`AD-02`), and serve resident MCP requests concurrently without cold `--once` single-flight locks (`AD-03`).
   - Hide `knowledge_query`, `knowledge_explain`, `knowledge_feedback`, `knowledge_distill`, and `knowledge_sync` from unactivated default `tools/list` responses (`AD-11`, `P2.1`), emitting `notifications/tools/list_changed` when `/mindie-agent` activates or revokes a manual session lease, and enforce per-session nonce + manual lease admission on every `knowledge_*` call.
   - Preserve Receipt Honesty (`isError=False` + `receipt_warning` + `receipt_unconfirmed=True`) when local bind/receipt accounting fails after a primary tool call succeeds, and precede every broad `except OSError:` with `except TimeoutError: raise`.

## 2. Isolated Verification Protocol

- Isolate `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `MINDIE_CONFIG_HOME`, `MINDIE_DATA_HOME`, `MINDIE_DIAGNOSTICS_ROOT`, `KIMI_CODE_HOME`, `REMOTE_DEV_AUTH_DIR`, `UV_CACHE_DIR`, and `TMPDIR` in all tests.
- Before committing, run:
  ```bash
  python3 .github/scripts/check_lf_eol.py
  python3 -m pytest -q -o "pythonpath=tests scripts" tests
  ```
