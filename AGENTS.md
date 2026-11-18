# MindIE Agent for Kimi Code (`mindie-agent/mindie-agent-kimi`) — Agent Operating Rules

This repository owns the thin Kimi Code host plugin (`kimi.plugin.json`), Kimi native wire/hook/transcript adaptation, and `0.1.31` five-file runtime built on the shared [`mindie-harness`](https://github.com/mindie-agent/harness) (`API_VERSION = 1`) substrate.

## 1. Core Architectural Invariants (`host-plugin`, `0.1.31`)

1. **Five-File Thin Boundary (`HOST-UPGRADE`)**:
   - `scripts/` contains **exactly** `mindie_launch.py`, `entry.py`, `kimi_transcript.py`, `install_kimi_plugin.py`, and `setup.py`, totaling **≤ 1,000 lines** (hard repository ceiling `≤ 1,200`). Never reintroduce `updater*.py`, `bridge.py`, `mcp_server.py`, `organizer*.py`, `paths.py`, or transition shims.
   - Delegate cold start, MCP serving/router, sharing consent, diagnostics, updates, process supervision, Windows Job Objects, PE verification, scheduled wrappers, atomic writes, and RSI utilities to `mindie_harness`; bundle the deterministic cold seed (`assets/mindie-harness-seed.pyz` + `assets/seed-lock.json`, `mindie-harness v0.1.3`).
   - `mindie_launch.py` is the only executable referenced by `kimi.plugin.json`: it resolves `kimi.json`/`state/kimi`, forwards `mcp <surface>`/`hook <event>` to `entry.py`, and verifies the seed before any cold provisioning. `setup.py init` writes `kimi.engine.json` (`root`, `domain="vllm-ascend"`, `admission_path`, absolute `transcript_adapter` -> `kimi_transcript.py`).
   - Keep every `scripts/*.py` docstring header (`[Role]` / `[Invariants]` / `[Timeouts & Locks]` / `[Side-Effect Policy]`) closing on or before line 30, and allow legitimate symlinks (`AD-07` — no `O_NOFOLLOW` hostility on config/repo reads).

2. **Windows 11 Zero-Popup (`HOST-NATIVE`)**:
   - Never pass `CREATE_NEW_CONSOLE` or `DETACHED_PROCESS`; every `subprocess.run` / `subprocess.Popen` passes `creationflags=_NO_WIN` with `CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0` defined in the same file.
   - Never top-level `import torch_npu`, never call unguarded `os.kill(pid, 0)`, and clean every temp path in `finally` (`unlink(missing_ok=True)` / `rmtree(..., ignore_errors=True)`).

3. **Fast Hooks, Silent Default-On MCP & Scoped Opt-In `Stop` (`HOST-WIRE`, `HOST-ADMISSION`)**:
   - `hook pretool` writes `{}` immediately and never reads stdin. `hook session-start` atomically enqueues `state/kimi/pending-update-check.json` and exits `{}` in `< 5ms`.
   - `hook stop` checks `mindie-consent.json` / `mindie-community.json` (`contribute` or `local-only`, non-empty absolute `project_roots`) and `os.path.commonpath` containment **before** importing any runtime; disabled or out-of-scope events exit `{}` in `< 15ms` with zero SQLite I/O. `entry.py` then re-checks `sharing_status` before provisioning a default lease (`not_before=0.0`) and calling `capture_hook(..., timeout=6.0)`.
   - Expose a single semantic `mindie-agent` skill (`"skills": "./skills/"`, no `disableModelInvocation`, no slash-command clutter). Keep `request_nonce` optional and keep MCP surfaces disjoint: `knowledge` -> five `knowledge_*` tools only; `remote` -> the 14 Kimi `remote_*` tools only; `components` -> `mindie_status`, `mindie_doctor`, diagnostics, `profiler`, `sim`, `coordinator`, `npu_top`, `debugger` only. Cross-surface calls must return `unknown_operation`.
   - Share `state/kimi/remote` across parent and subagent sessions (`AD-02`), serve resident MCP requests concurrently (`AD-03`), and initialize with `serverInfo.version == "0.1.31"`.
   - Preserve Receipt Honesty (`isError=False` + `receipt_warning` + `receipt_unconfirmed=True`) when local accounting fails after a primary tool call succeeds, and precede every broad `except OSError:` / `except Exception:` with `except TimeoutError: raise`.

## 2. Onboarding & Task Continuity

- Install with `uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host kimi`; `gh` is optional and never a blocker (`contribute` when signed in, otherwise `local-only`).
- First use runs three dynamic `AskUserQuestion` steps (workspace, contribution tier, optional one-step SSH NPU connection with `--password` at the end of terminal mode), then a `mindie_doctor` readiness check and one polite Star line.
- MindIE tools are accelerators, never roadblocks: if `remote-dev`, `coordinator`, `knowledge_*`, or `mindie_doctor` fails, report the concrete error once (reconciling uncertain remote writes first) and continue via native `ssh`/`scp` or task-local scripts.

## 3. Isolated Verification Protocol

- Isolate `HOME`, `USERPROFILE`, `APPDATA`, `LOCALAPPDATA`, `XDG_*`, `MINDIE_CONFIG_HOME`, `MINDIE_DATA_HOME`, `MINDIE_KIMI_CONFIG`, `MINDIE_AGENT_CONFIG`, `MINDIE_DIAGNOSTICS_ROOT`, `MINDIE_DIAGNOSTICS_CONFIG`, `KIMI_CODE_HOME`, `REMOTE_DEV_AUTH_DIR`, `UV_CACHE_DIR`, and `TMPDIR` in all tests.
- Before committing, run:
  ```bash
  python3 .github/scripts/check_lf_eol.py
  python3 -m pytest -q -o "pythonpath=tests scripts" tests
  python3 -m unittest discover -s tests/native -v
  python3 scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
  ```
