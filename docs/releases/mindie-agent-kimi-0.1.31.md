# mindie-agent-kimi 0.1.31：五文件瘦身架构与安装引导体验升级

[English](mindie-agent-kimi-0.1.31.en.md)

- **「大减法」：`scripts/` 仅保留 5 个薄文件（总计 ≤ 1,200 行）**：删除全部历史重复运行时模块（`updater*.py`、`bridge.py`、`mcp_server.py`、`organizer*.py`、`sharing.py`、`paths.py`、`kimi_launch.py` 等），仅保留 `mindie_launch.py`、`entry.py`、`kimi_transcript.py`、`install_kimi_plugin.py`、`setup.py`。冷启动、MCP 服务、共享授权、诊断、更新、进程监督与原子写入全部委托给共享基座 [`mindie-harness`](https://github.com/mindie-agent/harness)（`API_VERSION = 1`）。
- **唯一冷启动入口与零弹窗 Hook**：`kimi.plugin.json` 的三个 MCP 服务与 `PreToolUse` / `Stop` Hook 全部指向 `python3 ./scripts/mindie_launch.py`。`hook pretool` 不读取 stdin 立即返回 `{}`；`hook session-start` 以原子写入投递 `pending-update-check.json`；`hook stop` 在打开 SQLite 之前校验 `mindie-consent.json` / `mindie-community.json` 的贡献层级与 `project_roots`，使用 `os.path.commonpath` 做目录包含判断（`/work/app-other` 永不匹配 `/work/app`），未授权或越界时零 SQLite 快速退出。
- **MCP 工具面严格隔离**：`knowledge` 仅暴露 5 个 `knowledge_*` 工具；`remote` 仅暴露 Kimi 的 14 个 `remote_*` 工具（含 `remote_Read`、`remote_Bash`、`remote_probe`、`remote_auth`）；`components` 仅暴露 `mindie_status`、`mindie_doctor`、diagnostics、`profiler`、`sim`、`coordinator`、`debugger` 等组件工具。跨面调用返回 `unknown_operation`，`initialize` 上报 `serverInfo.version = "0.1.31"`。
- **安装与首次使用体验升级**：安装统一为 `uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host kimi`（`gh` 可选、绝不阻塞）。首次使用通过 3 个动态 `AskUserQuestion` 完成：工作区、贡献层级（已登录 `gh` 时推荐 `contribute`，否则 `local-only`）与可选的单步 SSH NPU 连接（终端模式末尾可用 `--password`），随后运行 `mindie_doctor` 做就绪检查，并附一句礼貌的 Star 提示。
- **非阻塞任务连续性规则**：`remote-dev`、`coordinator`、`knowledge_*`、`mindie_doctor` 是加速器而非路障。任一工具失败时只报告一次具体错误（重复写操作前先核对不确定的远端写入），随后立即改用原生 `ssh` / `scp` 或任务本地脚本继续任务。
- **文档、CI 与发布元数据同步**：`harness-compatibility.json` 的 `used_interfaces` / `code_paths` / `test_ids` 全部指向 5 个保留脚本、`skills/mindie-agent/SKILL.md` 与新的契约测试；CI 直接校验 `entry.verify_bundled_seed()`、`scripts/setup.py release-metadata`、原生 Node 启动测试与 `pytest`；发布说明与徽章统一升级到 `0.1.31`。
