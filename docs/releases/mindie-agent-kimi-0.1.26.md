# mindie-agent-kimi 0.1.26：存储生命周期治理与存量历史包袱回收

- 组件锁定更新：`mindie-coordinator 0.7.11`、`mindie-debugger 0.1.3`、`mindie-diagnostics 0.5.15`、`mindie-knowledge 0.13.22`、`mindie-profiler 0.5.15`、`mindie-sim 0.9.15`、`remote-dev 0.11.12`。`runtime-requirements.txt` 保持不可变的引导基线，仅更新 `runtime/active-overrides.txt`。
- 插件升级不再重复构建两套 `.venv`：存在可用当前运行时解释器时，先提取 `generations/<sha>` 源码树，复用当前解释器完成组件目录发现与合并构建；只有不存在合并构建或无可用当前解释器（冷启动）时才构建 `generations/<sha>/.venv`。同卷受管 uv 缓存避免跨卷克隆放大。
- 暂存失败自动回收本次新建的孤立 `launch/<sha>` 与未发布的半成品 `generations/<sha>`，不再累积孤儿启动目录。
- 定时/后台更新在 Pre-Build 与切换成功后静默执行共享存量清扫：回收 68+ 代 `component-runtimes`、34+ 代 `generations`、49+ 项 `launch`、33+ 项 `receipts` 以及超时/强杀遗留的 `mindie-kimi-summary-*` 临时目录；严格保留当前代、`previous` 回滚代、`bootstrap` / `bootstrap-venv`、外部 plist/wrapper 仍指向的代、存活租约与跨插件借用根。
- 组件热更新与插件切换提交后，原子刷新已安装的 `mindie-front`、Kimi 托管插件清单、`installed.json` 的 `originalSource` 以及 macOS LaunchAgent / Windows 计划任务包装器，解除旧代被外部入口永久钉死；仅更新已存在且可确认属于 MindIE 的文件，不改变用户未启用的调度状态。
- 常驻 MCP 服务在生命周期内持有其所属运行代际的共享运行租约；摘要隔离目录迁移到带 owner PID 元数据的共享 `summary_scratch`，并在 `finally` 中确定性清理。
- 主切换成功后的宿主刷新或存量清扫局部占用只记录结构化 `receipt_warning` / `cleanup_warning`，不回滚已提交代际，也不写入单向 `fault` 自锁死。

关联共享组件：[mindie-knowledge 0.13.22](https://github.com/mindie-agent/knowledge/releases/tag/v0.13.22)、[remote-dev 0.11.12](https://github.com/mindie-agent/remote-dev/releases/tag/v0.11.12)、[mindie-diagnostics 0.5.15](https://github.com/mindie-agent/diagnostics/releases/tag/v0.5.15)。
