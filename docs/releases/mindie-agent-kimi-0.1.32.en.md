# mindie-agent-kimi 0.1.32: Pre-`0.1.31` Seamless Self-Upgrade Handoff Fix

[中文](mindie-agent-kimi-0.1.32.md)

- **Restore `v0.1.30 -> v0.1.32` self-upgrade runtime probe & transcript handoff**: Adds a thin `scripts/transcript.py` re-export shim so `v0.1.30` `updater_staging.probe_runtime` (`cli.load_transcript_adapter`) and legacy `kimi.engine.json` `transcript_adapter` references resolve cleanly.
- **Standalone `update/launch/<sha>/mindie_launch.py`, `update/current.json`, and legacy scheduler compatibility**: `mindie_launch.py` resolves its owning `update/generations/<sha>/scripts` tree when invoked from `update/launch/<sha>/mindie_launch.py`, honors `update/current.json` before `kimi.json` is rebound, and accepts legacy `updater check|status` scheduled-task invocations (delegating `check` to `setup.py update --background`).
- **Automated `n-1 -> n` self-upgrade regression tests**: Adds `tests/test_legacy_self_upgrade.py` covering `scripts/transcript.py` probe loading, standalone `update/launch/<sha>/mindie_launch.py` execution, `update/current.json` interpreter resolution, and `updater check|status`.
