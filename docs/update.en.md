# Automatic Update Mechanism

<a href="update.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-kimi` `0.1.32` **no longer bundles an updater**: the plugin runtime keeps only five thin files, and updates are fully delegated to [`mindie-harness`](https://github.com/mindie-agent/harness) through `mindie_harness.runtime.wheelhouse.update_runtime` and `mindie_harness.runtime.scheduler`. The channel selects the highest formal Release compatible with the actual `kimi --version`, excludes drafts and prereleases, and never downgrades.

---

## 1. How It Works

1. **Channel Discovery & Compatibility Validation**: `update_runtime` reads the official release channel and the version, exact source SHA, and compatibility envelope declared by `mindie-plugin-release.json`; the declaration must match the source `harness-compatibility.json`. Supported Kimi versions are `>=2.1.1 <=2.999.999` (including prerelease suffixes).
2. **Isolated Runtime Build**: candidate runtimes are staged under `~/.local/share/mindie-agent/runtimes/kimi/` using full commit SHAs from `runtime-requirements.txt` / `runtime/active-overrides.txt` and a locked `uv` environment; component floors are verified before and after the build, so a lower formal release can never replace a newer installed one.
3. **Atomic Activation & Rollback**: after a model-free smoke probe, the candidate is committed under the update lock by atomically writing `python` / `component_release` in `kimi.json`; the running entrypoint is never overwritten in place, and failures keep the previous runtime with a rollback receipt.
4. **OS Scheduled-Task Migration**: macOS `launchd` and Windows scheduled tasks are registered, migrated, and removed by `mindie_harness.runtime.scheduler`; wrappers use only `CREATE_NO_WINDOW` (`0x08000000`) and never flash a console.
5. **Passive Checks With Zero Side Effects**: background checks only validate releases, stage candidates, and switch when idle; they never start the knowledge service or sync public feeds (feed sync runs only on demand via `knowledge_sync`).

---

## 2. Operator Commands

```sh
python3 scripts/setup.py update                 # Run one bounded update check (stage and atomically switch to a compatible release)
python3 scripts/setup.py update --force         # Ignore backoff and re-check immediately
python3 scripts/setup.py update --offline       # Use only the local wheelhouse; no network access
python3 scripts/setup.py update --background    # Ask the host to run this check in the background
python3 scripts/setup.py doctor                 # Read-only runtime, component, contribution-tier, and remote status
```

The `mindie_status` MCP tool reports the active runtime and component versions at any time; `mindie_doctor` provides the full readiness check and `remediations`.

---

## 3. Failure Handling & Host Reload Boundary

- **Backoff Without Tight Retry Loops**: temporary network or certificate-trust failures retain the current installation and record `next_retry_at` for a later normal check; TLS verification stays enabled. Content, version, tag, or compatibility mismatches fail before activation, and receipts keep only credential-scrubbed bounded reasons and static categories.
- **Host Reload Boundary**: after a successful update, the next `tools/list` / `tools/call` and Hook child use the new runtime; Kimi's startup-loaded Skill/MCP catalog and tool cache refresh only on a natural restart or new session, so the plugin never claims an old session's in-memory definitions changed. Running tasks are never force-killed or reloaded.
- **Legacy Migration**: pre-`0.1.32` `update/current.json` and generation trees remain read-only; the new substrate treats `kimi.json` `python` / `component_release` as authoritative and no longer reads `current.json`. If an old config points to the deleted `scripts/transcript.py`, `entry.py` rewrites `kimi.engine.json` `transcript_adapter` to `scripts/kimi_transcript.py` on the first `Stop`.
