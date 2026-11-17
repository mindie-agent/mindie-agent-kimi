# mindie-agent-kimi 0.1.28：接入 `mindie-harness` 共享底座、彻底根除 `CREATE_NEW_CONSOLE` 弹窗与保留 `0.1.26` 在线升级兼容

[English](mindie-agent-kimi-0.1.28.en.md)

- **接入 `mindie-harness v0.1.0` 共享底座与根除 Windows 控制台弹窗**：打包并校验 `assets/mindie-harness-seed.pyz` + `seed-lock.json`，将跨平台受管进程、Windows Job Object、PE `IMAGE_SUBSYSTEM_WINDOWS_GUI` 校验与 RSI 引擎统一委托至 `mindie_harness`，彻底移除全仓 `CREATE_NEW_CONSOLE` 调用。
- **`0.1.26` 在线平滑升级兼容桩与计划任务先迁移后回收**：保留 `< 30` 行轻量过渡路径兼容桩以支持存量客户端无感在线升级，确保 Windows 计划任务与 `launchd` 入口先原子切流至新代后再回收旧代（`Active + Rollback <= 2`）。
- **`0.1.13` 全量组件正式版锁定**：在 `runtime/active-overrides.txt` 与 `test-requirements.txt` 中升级锁定 `mindie-harness v0.1.0`、`mindie-knowledge v0.13.23`、`remote-dev v0.11.13`、`mindie-diagnostics v0.5.16`、`mindie-debugger v0.1.4`、`mindie-profiler v0.5.16`、`mindie-sim v0.9.18` 与 `mindie-coordinator v0.7.13`（保持 `runtime-requirements.txt` 初始引导三元组不变），并对齐 `--profile host-plugin` PR 门禁。
