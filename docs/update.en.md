# Automatic Update Mechanism

<a href="update.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

`mindie-agent-kimi` `0.1.33` **does not bundle a custom updater**: the plugin runtime keeps six canonical thin files, and updates are fully delegated to [`mindie-harness`](https://github.com/mindie-agent/harness) (`>= 0.1.5`) through `mindie_harness.runtime.wheelhouse.update_runtime` and `mindie_harness.runtime.scheduler`. The channel selects the highest formal Release compatible with the actual `kimi --version`, excludes drafts and prereleases, and never downgrades.

---

## 1. How It Works

1. **Channel Discovery & Compatibility Validation**: `update_runtime` reads the official release channel and the version, exact source SHA, and compatibility envelope declared by `mindie-plugin-release.json`; the declaration must match the source `harness-compatibility.json`. Supported Kimi versions are `>=2.1.1 <=2.999.999` (including prerelease suffixes).
2. **Isolated 9-Package Runtime Build**: candidate runtimes are staged under `runtimes/kimi/` against all 9 runtime packages pinned in root `pyproject.toml`, `uv.lock`, `product-contract.json`, and `runtime-requirements.txt` / `runtime/active-overrides.txt` (including `mindie-harness >= 0.1.5` and `npu-top 0.3.5`); component floors are verified before and after the build, so a lower formal release never replaces a newer installed one.
3. **Atomic Activation, 9-Domain Binding & Rollback**: after a model-free smoke probe, activation acquires `.kimi.json.update.lock`, runs `ensure_knowledge_binding(..., already_locked=True)` to provision content-addressed 9-domain `engines/kimi/<20hex>/<domain>.engine.json` manifests, and atomically updates `python`, `component_release`, and `domain_configs` in `kimi.json`; failures keep the previous runtime with a rollback receipt.
4. **Safe Legacy Scheduled-Task Retirement**: `setup.py init` and `setup.py update` query and delete legacy `MindIEKimiUpdate` Windows scheduled tasks (and macOS `launchd` `agent.mindie.kimi-update` / Linux `systemd` `mindie-kimi-update` units) before unlinking old `.pyw`/`.xml` wrappers; managed schedules use `mindie_harness.runtime.scheduler` with `CREATE_NO_WINDOW`.
5. **Passive Checks With Zero Side Effects**: background checks only validate releases, stage candidates, and switch when idle; they never start the knowledge service or sync public feeds (feed sync runs only on demand via `knowledge_sync`).

---

## 2. Operator Commands

```sh
uv run --no-project python scripts/setup.py update                 # Run one bounded update check (stage and atomically switch to a compatible release)
uv run --no-project python scripts/setup.py update --force         # Ignore backoff and re-check immediately
uv run --no-project python scripts/setup.py update --offline       # Use only the local wheelhouse; no network access
uv run --no-project python scripts/setup.py update --background    # Ask the host to run this check in the background
uv run --no-project python scripts/setup.py doctor                 # Read-only runtime, component, contribution-tier, and remote status
```

The `mindie_status` MCP tool reports the active runtime and component versions at any time; `mindie_doctor` provides the full readiness check and `remediations`.

---

## 3. Failure Handling & Host Reload Boundary

- **Backoff Without Tight Retry Loops**: temporary network or certificate-trust failures retain the current installation and record `next_retry_at` for a later normal check; TLS verification stays enabled. Content, version, tag, or compatibility mismatches fail before activation, and receipts keep only credential-scrubbed bounded reasons and static categories.
- **Host Reload Boundary**: after a successful update, the next `tools/list` / `tools/call` and Hook child use the new runtime; Kimi's startup-loaded Skill/MCP catalog and tool cache refresh only on a natural restart or new session, so the plugin never claims an old session's in-memory definitions changed. Running tasks are never force-killed or reloaded.
- **Validated `kimi.json` Precedence & Legacy Fallback**: when both a validated canonical `kimi.json` (`component_release/runtime.json` `validated` or usable `python`) and a legacy `update/current.json` exist, `mindie_launch.py` prefers `kimi.json` so stale `current.json` files never shadow newer wheelhouse upgrades, falling back to `update/current.json` only when `kimi.json` is absent or its interpreter is missing, while keeping the thin `scripts/transcript.py` forwarding shim for `v0.1.30` self-upgrade probes.
