# Automatic Update Mechanism

<a href="update.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-kimi` tracks the `main` branch of the plugin remote repository (defaulting to `https://github.com/mindie-agent/mindie-agent-kimi.git`, overridable via `update_remote` in adapter config or `MINDIE_KIMI_UPDATE_REMOTE`) and performs model-free, immutable generation updates in the background.

---

## 1. How Immutable Generation Updates Work

1. **OS Scheduler Trigger**: On macOS, a `launchd` agent (`agent.mindie.kimi-update`) runs the updater every 5 minutes (`schtasks` registration is implemented on Windows and tracked as a separate hardware verification item). No background daemon or model call is involved.
2. **Isolated Generation Staging**: Each check is serialized by a non-blocking `check.lock` and bounded by a 240-second deadline. It resolves remote `main` to an exact commit SHA and stages `update/generations/<sha>` with immutable code, a dedicated pinned virtualenv built from `runtime-requirements.txt` (full commit SHAs only), and generation-specific engine config—leaving user consent, community scope, and `state_dir` untouched.
3. **Single Atomic Pointer (`update/current.json`)**: `current.json` stores one atomic tuple `{generation, python, adapter_config, sha}` so code, interpreter, and configuration always come from the same committed generation.
4. **Versioned Persistent Launchers**: Each generated host package points to `update/launch/<sha>/mindie_launch.py` (with initial setup at `update/launch/bootstrap/`). Staging never mutates live entrypoints, and previously loaded launchers remain callable across updates.
5. **Exclusive Cutover & Native Registry Readback**: Under the exclusive operation lock, the updater calls `stop_if_idle` on the current service, installs the new package through Kimi's native plugin API with full readback verification (ID, version, enabled state, and source path), and then flips `current.json`. If verification fails, both the pointer and the native package roll back and record `rollback` in status.
6. **Independent Knowledge Feed Sync**: Under a shared operation lock, `mindie_knowledge.loop.cli sync` refreshes the public knowledge feed even when the plugin code itself is unchanged.

---

## 2. Operator Commands

```sh
python3 scripts/updater.py check               # Run one bounded update check immediately
python3 scripts/updater.py status              # Offline read-only check of current SHA, last result, and errors
python3 scripts/updater.py recover             # Clear quarantined SHA or unknown install state only
python3 scripts/updater.py install-schedule    # Register the OS scheduled task
python3 scripts/updater.py uninstall-schedule  # Remove the OS scheduled task before uninstalling
```

---

## 3. Failure Handling & Host Reload Boundary

- **Backoff Without Tight Retry Loops**: Temporary network or certificate-trust failures keep the existing installation active and record `next_retry_at` for future scheduled runs (with TLS verification always enabled). Corrupted candidate content is quarantined until explicitly cleared via `recover`.
- **Host Reload Boundary (`needs_host_reload`)**: After `current.json` switches, every new MCP call and Hook invocation dispatches to the new generation immediately. Because Kimi refreshes its top-level skill/command catalog on host reload, the updater sets `needs_host_reload: true` rather than claiming in-memory host UI state changed.
