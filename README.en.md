<p align="center">
  <img src="https://raw.githubusercontent.com/mindie-agent/mindie-agent/main/assets/brand/mindie-agent-logo.png" alt="MindIE Agent logo" width="128" height="128">
</p>

# MindIE Agent for Kimi Code

[![License: MIT](https://img.shields.io/badge/license-MIT-0F766E.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-2563EB.svg)](runtime-requirements.txt)
[![Host: Kimi Code](https://img.shields.io/badge/host-Kimi%20Code-7C3AED.svg)](kimi.plugin.json)

[中文](README.md) | English

The native **Kimi Code** plugin adapter for **MindIE Agent**, purpose-built for **Ascend inference and operator engineering** with local domain knowledge retrieval, privacy-gated community experience sharing, general SSH remote development (`remote-dev`), and structured fault diagnostics.

This repository inherits all nine [VAWS / MindIE Agent design principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md); retiring the legacy runtime does not retire or replace those principles. Built as a thin native Kimi plugin backed by the shared `mindie-knowledge` runtime, this repository owns Kimi session identity, `wire.jsonl` transcript parsing, MCP dispatch, and the optional K3 metadata runner.

> **Ecosystem & Release Status**: The initial `v0.0.1` MindIE Agent product release focuses on Codex (`mindie-agent-codex`) and Claude Code (`mindie-agent-cc`). `mindie-agent-kimi` is maintained in the MindIE Agent ecosystem as an experimental, source-installed native adapter; see the [Documentation Index](#documentation-index) for verification scope and acceptance boundaries.

---

## Key Features

- **On-Demand Domain Knowledge Retrieval**: Fast local retrieval powered by SQLite FTS5 (with `contentless_delete` support) for Ascend inference and operator engineering. Knowledge remains informational and never enforces rigid workflows.
- **Local Experience Loop & Privacy-Gated Community Sharing**:
  - Current capture uses the harness's public transcript projection followed by local deterministic redaction. Complete user and visible assistant messages are saved without tool payloads or a body-writing model.
  - Setup and updates automatically install the scanner and optional K3 metadata worker. The metadata worker returns only title and summary (with minimal/disabled thinking and no extra user configuration); failure leaves a source excerpt.
  - Community sharing defaults to off and activates only after explicit public opt-in. Read the [user notice / 用户须知](skills/mindie-agent/references/user-notice.md) for sharing scope, redaction limits, model-provider processing, and public Git history.
  - After presenting the user notice during setup, the entry recommends [mindie-agent/vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git) and asks whether to create and initialize a working directory (optional, reusing existing choices; see [workspace setup](skills/mindie-agent/references/workspace-setup.md)).
- **SSH Remote Ascend Development (`remote-dev`)**: General remote command execution, file synchronization, and bounded job polling over SSH—available independently of knowledge activation and contribution settings.
- **Structured Fault Diagnostics & Model-Free Updates**: Task-isolated read-only status inspection, optional sanitized product fault reporting, and OS-scheduled atomic generation updates with native registry readback and rollback receipts.

---

## Quick Start

### 1. Prerequisites & Installation

Requires Python 3.11+, Git, and an installed, signed-in Kimi Code with native plugin support.

The knowledge interpreter also requires **SQLite 3.43.0 or newer** compiled with FTS5 and `contentless_delete` support. Installation checks the actual SQLite library directly; the Python version alone does not establish this capability.

```sh
git clone https://github.com/mindie-agent/mindie-agent-kimi.git
cd mindie-agent-kimi
python3 scripts/setup.py
```

Setup builds the pinned runtime in persistent MindIE data, writes MindIE's configuration, leaves community sharing off, and registers the model-free update check. `--knowledge-python /absolute/path/to/python` is an optional operator override. Use `--no-schedule` for an isolated development install.

Setup prints `native_package`. Install that exact package through the host:

```sh
python3 scripts/install_kimi_plugin.py \
  --kimi-home "${KIMI_CODE_HOME:-$HOME/.kimi-code}" \
  --plugin-root /absolute/native_package/from/setup
```

Replace the package placeholder with the exact `native_package` printed by setup. After installation succeeds, the downloaded source can be moved or removed; keep the persistent runtime and launchers. The helper uses Kimi's native plugin API and does not fabricate the host's installed-plugin registry. The generated package points to the persistent launcher, so already-loaded entrypoints remain callable across updates.

- **Default configuration directory**: `~/.config/mindie-agent/`
- **Default local runtime data directory**: `~/.local/share/mindie-agent/kimi`
- Setup supports `--community-*` flags after installation; no reinstall or hand-edited JSON is needed to opt in later.

See [framework stability and verification](docs/framework-stability.en.md) for current behavior, reproducible checks, and acceptance boundaries.

### 2. First Use & Commands

Invoke `/mindie-agent` (or via skill activation `/skill:mindie-agent`, or command alias `/mindie-agent:init` / `/init`). The entry reuses approved values and asks only for missing public destination, account, or project scope.

- **Enabling community sharing**: Name the public repository (`owner/repo`) and account in your reply—or run `/mindie-agent contribute owner/repo ACCOUNT` (or `/mindie-agent:sharing-enable`)—to enable public sharing for the current project. The configured experience loop processes eligible `Stop` events automatically. Task binding and processing receipts are distinct. Explicit disable and legacy declined settings remain disabled until changed; read-only/later are no longer product choices.
- **With sharing off**: There is no `Stop` transcript collection, capture, or organizer model call. Public knowledge synchronization and `remote-dev` remain available.

| Kimi Code Command | Description |
|---|---|
| `/mindie-agent` (or `/skill:mindie-agent`, `/mindie-agent:init`, `/init`) | Bind the current native Kimi task, ensure the background service, and return current configuration state |
| `/mindie-agent:status` (or `/status`) | Offline status for the bound task: knowledge configuration, service/storage health, and the latest 5 captures and contribution batches |
| `/mindie-agent:deactivate` (or `/deactivate`) | Unbind MindIE Agent knowledge access from the current session (install-level choices remain untouched) |
| `/mindie-agent:recover` (or `/recover`) | Optional inspection of a specific contribution batch (`--batch <id>`); transient network failures are recovered automatically by the background worker |
| `/mindie-agent:sharing-enable` / `/mindie-agent:sharing-disable` / `/mindie-agent:sharing-status` | Explicitly opt in to or turn off public community contribution, or check sharing status without starting a model |
| `/mindie-agent:reporting-status` / `/mindie-agent:reporting-enable` / `/mindie-agent:reporting-disable` | Inspect, enable, or disable independent sanitized product failure reporting |

The native entry handles task identity and tool binding internally; users do not manage nonces or leases. `remote-dev` remains available independently of knowledge entry and contribution settings.

---

## Automatic Updates

This adapter updates its local-path plugin package through the native host API, driven by the OS scheduler (`launchd` every 5 minutes on macOS; Windows registration unverified). Each check resolves remote `main` to one SHA, stages an immutable generation with its own pinned venv, asks shared core `stop_if_idle` under the exclusive operation lock, installs through Kimi's native plugin API with readback, and atomically flips the generation pointer—rollback receipt included. For full details, status/recovery commands, and the host-reload boundary, see [docs/update.en.md](docs/update.en.md) ([中文](docs/update.md)).

The following selects a retained launcher using installed configuration, so it works even after the downloaded source checkout is removed:

```sh
MINDIE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/kimi.json"
MINDIE_LAUNCHER=$(python3 - "$MINDIE_CONFIG" <<'PYCODE'
import json, sys
from pathlib import Path
adapter = json.loads(Path(sys.argv[1]).read_text())
update = Path(adapter["state_dir"]) / "update"
current = json.loads((update / "current.json").read_text())
print(update / "launch" / (current["sha"] or "bootstrap") / "mindie_launch.py")
PYCODE
)
python3 "$MINDIE_LAUNCHER" --config "$MINDIE_CONFIG" updater status
```

- Replace `status` with `check` to check `main`, or `uninstall-schedule` to stop automatic checks (`install-schedule` restores scheduling).
- A known temporary network or certificate-trust failure is retried by the next scheduled check when its recorded retry time is due; TLS validation stays enabled.
- `recover` is only for quarantined content or an unknown failure, not the normal network path.

---

## Optional Product Failure Reporting

Product failure reporting is a separate user choice from knowledge contribution. It stays off until an explicit native command enables it, and the setting is shared across all MindIE adapters:

- `/mindie-agent:reporting-status` reads the setting and reporter state.
- `/mindie-agent:reporting-enable` enables sanitized product fault reporting to `mindie-agent/mindie-agent` and returns an exact command to prepare the reporter. Run that command once outside the Hook and check the result before treating the reporter as ready: runtime ready and worker healthy must both be present. A failed preparation is not retried automatically.
- `/mindie-agent:reporting-disable` revokes future reporting; local diagnostics remain.

`not_configured` describes upload consent, not whether local logs exist. Enabling or disabling this shared user setting affects all MindIE adapters.

Incidents contain static product stages, error types, and installed code versions. They do not collect task transcripts, prompts, commands, environment, or credentials. Cancellation, caller validation, inactive permissions, ordinary lock contention, and business-command failures are not product bug reports. The original tool result and remote job reference stay available when a diagnostic reference is added.

Updater checks perform bounded offline log maintenance even with reporting off. `launchd` output goes to the null device; updater state and bounded diagnostics carry failure evidence, and no unbounded `scheduler.log` is created.

---

## Stop or Uninstall

- `/mindie-agent:deactivate` ends the current task's knowledge access.
- `/mindie-agent:sharing-disable` stops contribution for the configured scope.
- Neither command uninstalls the plugin or stops model-free updates.

Before removing the plugin, close tasks using it and run the retained launcher's `updater uninstall-schedule`. A failed cancellation preserves the plist and reports failure. Then remove MindIE Agent in Kimi's native Plugins UI. Keep runtime data, receipts, and old launchers while they may still be in use; do not recursively remove shared data. Uninstalling this adapter does not disable the shared reporter; disable reporting separately only if that is the desired choice across all adapters.

---

## Documentation Index

See the complete documentation index at [docs/README.en.md](docs/README.en.md) ([中文](docs/README.md)):

| Document (English) | 中文版本 | Description |
|---|---|---|
| [Automatic Update](docs/update.en.md) | [自动更新机制](docs/update.md) | Immutable generation staging, atomic pointer switch, rollback receipts, CLI commands, and host reload boundary |
| [Framework Stability](docs/framework-stability.en.md) | [框架稳定性与验证](docs/framework-stability.md) | Everyday usage contract, preflight & test suite execution, Windows atomic config I/O, and acceptance boundaries |
| [Failure Diagnostics](docs/failure-diagnostics.en.md) | [故障诊断与本地取消](docs/failure-diagnostics.md) | Read-only status diagnostics, local process cancellation & EOF cleanup, and remote job reference preservation |
| [Organizer Diagnostics](docs/organizer-diagnostics.en.md) | [整理器故障与取消证据](docs/organizer-diagnostics.md) | Static exit-code categories, process-group lifecycle management, and isolated organizer validation |
| [Service Continuity](docs/service-continuity.en.md) | [插件更新期间的服务连续性](docs/service-continuity.md) | Knowledge service stop/restore across updates, rollback handoff, and daemon acceptance boundaries |
| [Test Boundaries](docs/test-boundaries.en.md) | [测试边界与故障检测](docs/test-boundaries.md) | Cross-platform CI check ordering, independent failure dimensions, and stdio transport boundary tests |
| [Windows Launch Contract](docs/windows-launch-contract.en.md) | [Windows 原生启动契约](docs/windows-launch-contract.md) | Windows shell-free MCP launch, Job object descendant ownership, and native boundary validation |

---

## License

Released under the [MIT License](LICENSE).
