# mindie-agent-kimi 0.1.32：Pre-`0.1.31` 在线平滑自升级交接兼容修复

[English](mindie-agent-kimi-0.1.32.en.md)

- **恢复 `v0.1.30 -> v0.1.32` 在线自升级探针与启动器交接**：补齐轻量 `scripts/transcript.py` 转发桩，确保 `v0.1.30` `updater_staging.probe_runtime`（`cli.load_transcript_adapter`）与历史 `kimi.engine.json` `transcript_adapter` 解析 100% 通过。
- **独立 `update/launch/<sha>/mindie_launch.py`、`update/current.json` 与历史计划任务兼容**：`mindie_launch.py` 在从 `update/launch/<sha>/mindie_launch.py` 独立执行时自动锚定对应 `update/generations/<sha>/scripts`，在未重绑 `kimi.json` 前自动识别 `update/current.json` 中的候选代解释器，并兼容历史 `launchd` / Windows 计划任务的 `updater check|status` 调用（委托给 `setup.py update --background`）。
- **自动化 `n-1 -> n` 自升级契约回归测试**：新增 `tests/test_legacy_self_upgrade.py`，端到端覆盖 `scripts/transcript.py` 探针加载、`update/launch/<sha>/mindie_launch.py` 独立启动、`update/current.json` 切流解释器解析及 `updater check|status` 兼容路径。
