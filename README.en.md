<p align="center">
  <img src="https://raw.githubusercontent.com/mindie-agent/mindie-agent/main/assets/brand/mindie-agent-logo.png" alt="MindIE Agent logo" width="128" height="128">
</p>

<h1 align="center">MindIE Agent for Kimi Code</h1>

<p align="center">
  <b>Native Kimi Code Plugin for Ascend Inference & Operator Engineering</b><br/>
  <sub>On-Demand Domain Knowledge · SSH Remote NPU Development · Privacy-Gated Experience Loop</sub>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-0F766E.svg" alt="License: MIT"></a>
  <a href="runtime-requirements.txt"><img src="https://img.shields.io/badge/python-3.11%2B-2563EB.svg" alt="Python 3.11+"></a>
  <a href="kimi.plugin.json"><img src="https://img.shields.io/badge/host-Kimi_Code-7C3AED.svg" alt="Host: Kimi Code"></a>
</p>

<p align="center">
  <a href="README.md">中文</a> | <b>English</b> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#why-use-this-plugin">What Can It Do?</a> ·
  <a href="#everyday-usage--command-reference">Commands</a> ·
  <a href="#architecture--component-map">Architecture</a> ·
  <a href="docs/README.en.md">Documentation</a>
</p>

---

**MindIE Agent for Kimi Code** (`mindie-agent-kimi`) is the native Kimi Code plugin adapter for [MindIE Agent](https://github.com/mindie-agent/mindie-agent). It brings **`vLLM-Ascend` domain knowledge search**, **SSH remote Ascend NPU execution**, and a **privacy-gated experience loop** directly into your Kimi Code workflow.

Designed around the [MindIE Agent Engineering Design Principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.en.md), this thin adapter handles Kimi session identity, `wire.jsonl` public transcript extraction, MCP dispatch, and atomic generation updates, while delegating core knowledge, remote execution, and diagnostics to shared components.

> **Ecosystem & Release Note**: The initial `v0.0.1` MindIE Agent product release targets OpenAI Codex CLI ([`mindie-agent-codex`](https://github.com/mindie-agent/mindie-agent-codex)) and Claude Code ([`mindie-agent-cc`](https://github.com/mindie-agent/mindie-agent-cc)); `mindie-agent-kimi` is maintained in the ecosystem as an experimental, source-installed native adapter. See the [Documentation Index](#documentation-index) for verification scope.

---

## Why Use This Plugin?

Here are three concrete ways `mindie-agent-kimi` helps during daily Ascend inference and operator development:

### Scenario 1: Search Verified `vLLM-Ascend` Troubleshooting Cases (`mindie-knowledge`)
> **The Problem**: When you hit an `HCCL` communication timeout, an `ACL` operator error, a Graph Mode compilation failure, or numerical drift, hunting through scattered GitHub Issues and chat logs takes hours.  
> **How It Helps**: Paste the traceback or symptom directly into Kimi Code. The plugin queries locally synced [`knowledge-vllm-ascend`](https://github.com/mindie-agent/knowledge-vllm-ascend) packages via SQLite FTS5 and returns verified troubleshooting cases complete with **applicable environments, evidence, and known caveats**—as optional reference material rather than a rigid workflow.

### Scenario 2: Edit Locally, Build & Debug on Remote Ascend NPU Servers (`remote-dev`)
> **The Problem**: Your daily machine is a macOS or Windows laptop while the Ascend NPU hardware lives on a remote Linux server. Manually syncing patches and tailing long test runs slows down every iteration.  
> **How It Helps**: Ask Kimi to *"sync my local changes to the remote Ascend server, run the unit tests, and watch the logs."* The built-in `remote-dev` MCP server supports remote command execution, file synchronization, and bounded background job polling over SSH—working independently even if knowledge capture is disabled.

### Scenario 3: Turn Debugging Sessions into Sanitized Community Knowledge (Opt-In)
> **The Problem**: Hard-won debugging insights usually vanish when you close the terminal, yet sharing raw transcripts risks leaking internal IPs, keys, or private file paths.  
> **How It Helps**: Community sharing is **off by default**. Only when you explicitly opt in for an approved public repository does the `Stop` hook extract visible user/assistant messages, run local deterministic redaction and Gitleaks scanning, generate navigation metadata via an isolated organizer, and prepare a sanitized knowledge PR.
> - Read the **[User & Contribution Notice](skills/mindie-agent/references/user-notice.md)** for full details on sharing scope, redaction limits, and public Git history.
> - To set up a multi-task Git worktree layout for `vllm` and `vllm-ascend`, see **[Workspace Setup](skills/mindie-agent/references/workspace-setup.md)** and [vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git).

---

## Quick Start

### Option 1: One-Sentence Install via Your AI Agent (Recommended)

Copy and paste the prompt below directly to Kimi Code (or any terminal-capable AI Agent) and let it build and install the plugin for you:

```text
Please install https://github.com/mindie-agent/mindie-agent-kimi for me
```

<details open>
<summary>🤖 <b>Automated Installation Protocol for AI Agents (Instructions for AI Agents)</b></summary>

If you are an AI Agent asked by the user to install this plugin, execute the following deterministic steps directly in the terminal **without asking the user to read the documentation manually**:

1. **Check prerequisites and SQLite FTS5 support**:
   ```sh
   git --version
   kimi --version
   python3 -c "import sqlite3; assert sqlite3.sqlite_version_info >= (3, 43, 0), sqlite3.sqlite_version; print('SQLite OK:', sqlite3.sqlite_version)"
   ```
   - Confirm Python >= 3.11 and that the linked SQLite library is `>= 3.43.0` (compiled with FTS5 and `contentless_delete` support).

2. **Clone the repository, build the pinned runtime, and register via Kimi's native plugin API**:
   ```sh
   git clone https://github.com/mindie-agent/mindie-agent-kimi.git /tmp/mindie-agent-kimi
   cd /tmp/mindie-agent-kimi
   SETUP_JSON=$(python3 scripts/setup.py)
   echo "$SETUP_JSON"
   NATIVE_PKG=$(printf '%s' "$SETUP_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin)['native_package'])")
   python3 scripts/install_kimi_plugin.py \
     --kimi-home "${KIMI_CODE_HOME:-$HOME/.kimi-code}" \
     --plugin-root "$NATIVE_PKG"
   ```

3. **Verify native registry readback and report back**:
   ```sh
   python3 scripts/install_kimi_plugin.py \
     --kimi-home "${KIMI_CODE_HOME:-$HOME/.kimi-code}" \
     --plugin-root "$NATIVE_PKG" \
     --readback
   ```
   - Confirm `mindie-agent` shows `enabled: true`, `state: "ok"`, and both MCP servers (`knowledge` and `remote`) enabled. Report completion to the user (the temporary clone `/tmp/mindie-agent-kimi` can be removed; the persistent runtime lives under `~/.local/share/mindie-agent/kimi`).

</details>

### Option 2: One-Line Terminal Install (Manual)

To install manually from your terminal:

```sh
git clone https://github.com/mindie-agent/mindie-agent-kimi.git && \
cd mindie-agent-kimi && \
NATIVE_PKG=$(python3 scripts/setup.py | python3 -c "import json,sys; print(json.load(sys.stdin)['native_package'])") && \
python3 scripts/install_kimi_plugin.py --kimi-home "${KIMI_CODE_HOME:-$HOME/.kimi-code}" --plugin-root "$NATIVE_PKG"
```

#### Environment & Storage Notes
- **SQLite Requirement**: The Python interpreter used for the knowledge engine must link **SQLite `>= 3.43.0`** with FTS5 and `contentless_delete` support (pass `--knowledge-python /path/to/python` to `setup.py` to specify an explicit interpreter).
- **Persistent Paths**: Configuration is stored in `~/.config/mindie-agent/kimi.json`, and the persistent runtime and launchers live under `~/.local/share/mindie-agent/kimi`. Once installed, the cloned source directory can be safely moved or removed.

---

## Everyday Usage & Command Reference

In Kimi Code sessions, `remote-dev` tools work on demand at any time. Use the slash commands below to bind knowledge access, inspect status, or update sharing preferences:

| Kimi Code Command | Description |
|---|---|
| `/mindie-agent` (or `/skill:mindie-agent`, `/mindie-agent:init`, `/init`) | Bind the current native Kimi task, ensure the background knowledge service is ready, and view or configure preferences (saved choices persist across sessions). |
| `/mindie-agent:status` (or `/status`) | **Read-only status check**: inspect knowledge configuration, service/storage health, and the latest 5 captures and contribution batches without starting a model. |
| `/mindie-agent contribute owner/repo ACCOUNT` | Explicitly configure opt-in public knowledge contribution for the current project. |
| `/mindie-agent:sharing-enable` / `/mindie-agent:sharing-disable` / `/mindie-agent:sharing-status` | Enable, pause, or inspect public community contribution (when disabled, `Stop` performs no transcript collection or organizer call). |
| `/mindie-agent:deactivate` (or `/deactivate`) | Unbind knowledge access from the current session while keeping install-level preferences intact. |
| `/mindie-agent:recover` (or `/recover`) | Inspect or recover a specific contribution batch (`--batch <id>`); transient network failures recover automatically. |
| `/mindie-agent:reporting-status` / `/mindie-agent:reporting-enable` / `/mindie-agent:reporting-disable` | Inspect, enable, or disable independent sanitized product fault reporting (`reporting-enable` prints a one-time terminal setup command). |

### Automatic Updates & Safe Uninstall

- **Scheduled Model-Free Updates**: On macOS, a `launchd` job checks remote `main` every 5 minutes, builds an immutable generation with its own pinned virtualenv, stops the old service when idle, registers the updated package through Kimi's native plugin API, and switches the generation pointer atomically with rollback receipts (see [Automatic Updates](docs/update.en.md)). Check update status at any time with:
  ```sh
  MINDIE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/kimi.json"
  MINDIE_LAUNCHER=$(python3 -c 'import json,sys,pathlib; cfg=json.loads(pathlib.Path(sys.argv[1]).read_text()); u=pathlib.Path(cfg["state_dir"])/"update"; cur=json.loads((u/"current.json").read_text()); print(u/"launch"/(cur["sha"] or "bootstrap")/"mindie_launch.py")' "$MINDIE_CONFIG")
  python3 "$MINDIE_LAUNCHER" --config "$MINDIE_CONFIG" updater status
  ```
  > Tip: Replace `status` with `check` to trigger an immediate check, or `uninstall-schedule` to remove the background schedule.
- **Safe Uninstall**: Close active tasks using the plugin, run `python3 "$MINDIE_LAUNCHER" --config "$MINDIE_CONFIG" updater uninstall-schedule`, and then remove `mindie-agent` in Kimi's native Plugins UI.

---

## Architecture & Component Map

`mindie-agent-kimi` is a **thin native adapter**: it manages Kimi plugin manifests, session identity, `wire.jsonl` transcript extraction, and local generation updates, while delegating domain knowledge, remote execution, and diagnostics to shared components.

```mermaid
flowchart LR
  Kimi["Kimi Code Host<br/>(Plugin API / Hooks / MCP stdio)"] --> Launcher["mindie-agent-kimi<br/>(Persistent Launcher & Session Bridge)"]
  Launcher --> Knowledge["mindie-agent/knowledge<br/>(SQLite FTS5 Search, Redaction & PR Loop)"]
  Knowledge --> Feed["mindie-agent/knowledge-vllm-ascend<br/>(vLLM-Ascend Knowledge Feed)"]
  Launcher --> RemoteDev["mindie-agent/remote-dev<br/>(SSH Remote Ascend Exec, Sync & Poll)"]
  Launcher --> Diagnostics["mindie-agent/diagnostics<br/>(Bounded Local Diagnostics & Opt-In Reporter)"]
```

| Repository / Component | Responsibility |
|---|---|
| [`mindie-agent/mindie-agent`](https://github.com/mindie-agent/mindie-agent) | Top-level architecture, MindIE Agent Engineering Design Principles, domain boundaries, and paired-release governance. |
| `mindie-agent/mindie-agent-kimi` (this repo) | Kimi native plugin manifest (`kimi.plugin.json`, `commands/`, `skills/`), `wire.jsonl` transcript parser, isolated organizer runner, and immutable generation updater (`scripts/`). |
| [`mindie-agent/knowledge`](https://github.com/mindie-agent/knowledge) | Shared knowledge core: local SQLite FTS5 search, deterministic redaction, capture queue, and GitHub PR publication receipts. |
| [`mindie-agent/knowledge-vllm-ascend`](https://github.com/mindie-agent/knowledge-vllm-ascend) | Curated `vllm-ascend` domain knowledge feed. |
| [`mindie-agent/remote-dev`](https://github.com/mindie-agent/remote-dev) | Bounded remote command execution, file synchronization, and background job polling over SSH for Ascend servers. |
| [`mindie-agent/diagnostics`](https://github.com/mindie-agent/diagnostics) | Bounded local diagnostic retention and optional sanitized GitHub Issue fault reporting. |

---

## Developer Verification

From the repository root, with the pinned runtime dependencies installed and the peer repository checkout available, run the preflight and contract test suite:

```sh
export MINDIE_PEER_CC_REPO=/path/to/mindie-agent-cc
python3 tests/preflight.py
python3 -m unittest discover -s tests -p test_runtime_contract.py
python3 -m unittest discover -s tests/native -v
python3 -m unittest discover -s tests
```

---

## Documentation Index

Detailed engineering guides and verification specifications are available in [`docs/`](docs/README.en.md) in both English and Chinese:

- **[Documentation Index (English)](docs/README.en.md)** | **[文档索引（中文）](docs/README.md)**
- **Automatic Update Mechanism**: [English](docs/update.en.md) | [中文](docs/update.md) — Immutable generation staging, atomic pointer switching, rollback receipts, and host reload boundaries.
- **Framework Stability & Verification**: [English](docs/framework-stability.en.md) | [中文](docs/framework-stability.md) — Persistent user preferences, Windows atomic config I/O, and preflight test workflow.
- **Failure Diagnostics & Local Cancellation**: [English](docs/failure-diagnostics.en.md) | [中文](docs/failure-diagnostics.md) — Read-only status checks, local process cleanup on cancel, and remote job reference preservation.
- **Organizer Diagnostics & Process Groups**: [English](docs/organizer-diagnostics.en.md) | [中文](docs/organizer-diagnostics.md) — Static exit-code categories, process-group lifecycle management, and isolated organizer verification.
- **Service Continuity Across Updates**: [English](docs/service-continuity.en.md) | [中文](docs/service-continuity.md) — Knowledge service stop/restore during updates, rollback handoff, and daemon acceptance boundaries.
- **Test Boundaries & Failure Detection**: [English](docs/test-boundaries.en.md) | [中文](docs/test-boundaries.md) — Cross-platform CI check ordering, independent failure dimensions, and stdio transport boundary tests.
- **Windows Native Launch Contract**: [English](docs/windows-launch-contract.en.md) | [中文](docs/windows-launch-contract.md) — Shell-free MCP launch on Windows, Job Object descendant cleanup, and native boundary verification.

---

## License

Released under the [MIT License](LICENSE).
