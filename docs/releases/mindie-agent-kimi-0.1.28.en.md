# mindie-agent-kimi 0.1.28: `mindie-harness` Substrate Adoption, Zero `CREATE_NEW_CONSOLE` Popups, and `0.1.26` Live-Upgrade Compatibility

[中文](mindie-agent-kimi-0.1.28.md)

- **Shared `mindie-harness v0.1.0` Substrate & Windows Zero-Popup Enforcement**: Bundles verified `assets/mindie-harness-seed.pyz` + `seed-lock.json`, delegates process supervision, Win32 Job Objects, PE `IMAGE_SUBSYSTEM_WINDOWS_GUI` verification, and RSI helpers to `mindie_harness`, and removes every `CREATE_NEW_CONSOLE` flag.
- **Seamless `0.1.26` Live-Upgrade Shims & Migrate-Before-GC**: Retains `< 30`-line transition path shims for deployed `0.1.26+` installations and migrates Windows Scheduled Tasks / `launchd` plists to the active generation before retiring superseded generations (`Active + Rollback <= 2`).
- **`0.1.13` Formal Component Baseline**: Updates `runtime/active-overrides.txt` and `test-requirements.txt` to `mindie-harness v0.1.0`, `mindie-knowledge v0.13.23`, `remote-dev v0.11.13`, `mindie-diagnostics v0.5.16`, `mindie-debugger v0.1.4`, `mindie-profiler v0.5.16`, `mindie-sim v0.9.18`, and `mindie-coordinator v0.7.13` (preserving the immutable `runtime-requirements.txt` bootstrap trio), aligned with `--profile host-plugin` governance.
