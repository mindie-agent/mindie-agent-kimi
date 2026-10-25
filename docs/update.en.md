# Automatic Update Mechanism

<a href="update.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-kimi` selects the highest compatible formal Release from its official repository using the actual `kimi --version`. Drafts and prereleases are excluded, and installed versions never downgrade. The `mindie-plugin-release.json` asset declares the version, exact source SHA and supported hosts; it must match the source `harness-compatibility.json`. Supported and verified hosts in `harness-compatibility.json` are Kimi `2.1.1`, `2.2.0`, and `2.3.1`. `update_remote` / `MINDIE_KIMI_UPDATE_REMOTE` may select a source mirror, but cannot change the official Release selection.

---

## 1. How Immutable Generation Updates Work

1. **OS Scheduler Trigger**: On macOS, a `launchd` agent (`agent.mindie.kimi-update`) runs the updater every 5 minutes (`schtasks` registration is implemented on Windows and tracked as a separate hardware verification item). No background daemon or model call is involved.
2. **Isolated Generation Staging**: Each check is serialized by a non-blocking `check.lock` and bounded by a 1200-second deadline (`CHECK_BUDGET = 1200.0s`). It verifies the formal Release tag against the declared commit SHA and checks the fetched version and compatibility declaration before installing dependencies and stages `update/generations/<sha>` with immutable code, a dedicated pinned virtualenv built from `runtime-requirements.txt` (full commit SHAs only), and generation-specific engine config—leaving user consent, community scope, and `state_dir` untouched.
3. **Single Atomic Pointer (`update/current.json`)**: `current.json` stores one atomic tuple `{generation, python, adapter_config, sha}` so code, interpreter, and configuration always come from the same committed generation.
4. **Versioned Persistent Launchers**: Each generated host package points to `update/launch/<sha>/mindie_launch.py` (with initial setup at `update/launch/bootstrap/`). Staging never mutates live entrypoints, and previously loaded launchers remain callable across updates.
5. **Exclusive Cutover & Native Registry Readback**: Under the exclusive operation lock, the updater calls `stop_if_idle` on the current service, installs the new package through Kimi's native plugin API with full readback verification (ID, version, enabled state, and source path), and then flips `current.json`. If verification fails, both the pointer and the native package roll back and record `rollback` in status.
6. **Independent Shared Component Release Updates (`component_update.py`)**: When no newer plugin Release is needed, the updater discovers formal GitHub Releases directly from official component repositories, stages a side-by-side `uv` runtime under `update/component-runtimes/` using `max(installed version, plugin baseline, latest formal component Release)` with a verified `uv.lock`, probes the runtime, retains `host-package` for rollback, and switches `current.json` when idle with `update_native=False` (without reinstalling native Kimi plugin definitions). When staging a newer plugin Release, component preparation also preserves the installed component floor before building `host-package`.
7. **No Passive Knowledge Service Startup or Feed Sync**: Background update checks (`updater.py check`) strictly validate releases, stage immutable generations, and cut over when idle; they never start the knowledge service or run public feed sync in passive background checks (public feed sync runs only inside sessions explicitly enabled via `/mindie-agent`).

---

## 2. Operator Commands

```sh
python3 scripts/updater.py check               # Run one bounded update check immediately
python3 scripts/updater.py status              # Offline read-only check of current SHA, last result, and errors
python3 scripts/updater.py recover             # Explicitly retry the check, retaining release and compatibility validation
python3 scripts/updater.py install-schedule    # Register the OS scheduled task
python3 scripts/updater.py uninstall-schedule  # Remove the OS scheduled task before uninstalling
```

On Windows, the task uses `pythonw.exe` from the same Python installation to launch a UTF-8 wrapper, keeping the updater and its console children hidden. Run `install-schedule` again to replace an existing `.cmd` task. Registration fails explicitly without changing the existing task if `pythonw.exe` is missing.

---

## 3. Failure Handling & Host Reload Boundary

- **Backoff Without Tight Retry Loops**: Temporary network or certificate-trust failures keep the existing installation active and record `next_retry_at` for future scheduled runs (with TLS verification always enabled). Content, version, tag or compatibility mismatches fail visibly before installation. An unsupported host reports `no_compatible_release`; the current generation and user authorization remain intact. For an unclassified child-command failure, only the local `failed.json` keeps one credential-scrubbed, length-bounded stderr reason; shared diagnostics still records only the static category and exit code. Kimi keeps `runtime-requirements.txt` as a consistent no-overrides bootstrap trio with knowledge 0.13.3 and remote-dev 0.10.6 (diagnostics 0.5.2 / `801a01b...`), so the immutable 0.1.2 controller's first hop resolves; candidate setup/CI then apply `runtime-active-overrides.txt` to select both diagnostics 0.5.4 (`5ae643e...`) and remote-dev 0.10.7 (`4c40cb5...`), repairing the fresh active runtime. component_prepare only preserves installed components or advances to newer formal releases, never downgrading; 0.5.4/4c are active candidate pins and formal feed discovery is not claimed before publication. If an existing cached runtime is still inconsistent, the one-time recovery path remains: run the candidate source/release `scripts/updater.py recover --config <existing config>` directly (the candidate builder uses `--overrides` and the same native install/rollback transaction), or run `scripts/setup.py` in a clean profile, after which scheduled updates resume.
- **Host Reload Boundary (`needs_host_reload`)**: The update receipt's `current.json` records the committed **dispatch generation**, not what an already running host has loaded in memory. After the pointer switch the stable launcher still reads `current.json` per call, so the next `tools/list`/`tools/call` and Hook child process use the new generation's launcher, interpreter and scripts. Existing task authorization, the previous generation tree and the previous launcher remain available; no running host or job is killed or force-reloaded. Kimi's startup-loaded Skill/command/MCP catalog and tool cache refresh only on a natural host restart or a new session/process, so the status keeps `needs_host_reload: true` and never claims the old session's in-memory definitions became the new active ones. A host package produced by the legacy 0.1.2/0.1.3 updater may lack `toolTimeoutMs` or the profile env; the new launcher binds the profile before discovery, and the next check rebuilds/reinstalls that package once from the legacy receipt without touching a running host.
