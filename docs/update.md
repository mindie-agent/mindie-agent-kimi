# 自动更新机制

<b>中文</b> | <a href="update.en.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-kimi` 通过操作系统定时任务选择官方仓库中与实际 `kimi --version` 兼容的最高正式 Release，排除 draft 和 prerelease，且不会降级。`mindie-plugin-release.json` 声明版本、精确源 SHA 和兼容范围；源码中的 `harness-compatibility.json` 必须与发布附件一致。当前有实机依据的宿主版本为 2.1.1 和 2.2.0。源码镜像可由 `update_remote` / `MINDIE_KIMI_UPDATE_REMOTE` 指定，但不能改变官方 Release 的选择结果。

---

## 1. 工作原理与原子切换流程

1. **系统调度触发**：在 macOS 上通过 `launchd`（`agent.mindie.kimi-update`）每 5 分钟运行一次更新检查（Windows 计划任务 `schtasks` 代码已实现，待独立实机验收）。全程无需常驻守护进程，也不消耗任何大模型调用。
2. **非阻塞锁与独立代数构建**：单次检查受非阻塞 `check.lock` 与 240 秒总超时保护。更新器校验正式 Release tag 指向声明的 Commit SHA，并在安装依赖前核对取回源码中的插件版本与兼容声明，并在 `update/generations/<sha>` 下构建不可变目录：包含源码、基于 `runtime-requirements.txt`（仅允许完整 Commit SHA）独立创建的锁定虚拟环境，以及对应的 `config/` 配置。用户的准入记录、社区贡献授权与状态目录保持不变。
3. **单一原子指针（`update/current.json`）**：`current.json` 记录唯一生效的四元组 `{generation, python, adapter_config, sha}`。每次调用子进程时，启动器都会注入该四元组对应的配置与解释器，确保代码、Python 环境与配置始终来自同一个完整代数。
4. **持久化启动器路径**：每次构建生成的宿主插件清单均指向带版本哈希的启动器路径（`update/launch/<sha>/mindie_launch.py`，初始引导位于 `update/launch/bootstrap/`）。构建新版本绝不原地覆盖正在运行的入口，已加载的旧启动器路径依然可正常调用并自动转发至最新提交的代数。
5. **排他锁切换与原生回读校验**：切换阶段获取排他操作锁，先调用当前核心的 `stop_if_idle` 安全停止空闲服务，再通过 Kimi 原生插件 API 安装新插件包并回读校验 ID、版本号、启用状态与源路径，确认无误后原子翻转 `current.json`。若安装失败，自动回滚指针与原生插件包，并在状态回执中如实记录 `rollback`。
6. **独立的公共知识同步**：无论插件代码是否有更新，更新器都会在共享锁下调用 `mindie_knowledge.loop.cli sync` 增量同步公共知识源（不触发整理器或模型调用）。

---

## 2. 常用管理命令

```sh
python3 scripts/updater.py check               # 立即执行一次有界更新检查
python3 scripts/updater.py status              # 离线只读查看当前生效版本、最近结果与回滚记录
python3 scripts/updater.py recover             # 显式重试本次检查，仍执行正式发布与兼容性校验
python3 scripts/updater.py install-schedule    # 注册系统定时更新任务
python3 scripts/updater.py uninstall-schedule  # 移除系统定时更新任务（卸载插件前执行）
```

---

## 3. 容错退避与宿主重载边界

- **失败退避与防死循环**：单次检查内部不做循环重试。若遇到临时网络波动或限流，保留当前完好版本并记录 `next_retry_at`，交由后续定时检查按退避时间重试（全程保持 TLS 证书校验开启）。内容、版本、tag 或兼容声明不一致时不会安装，错误会显示在状态中。没有兼容正式版本时报告 `no_compatible_release`；当前代数与用户授权保持不变。
- **宿主重载说明（`needs_host_reload`）**：代数指针切换后，后续每次 MCP 工具调用与 Hook 触发都会立即使用新版本的解释器与脚本；而 Kimi 宿主界面缓存的斜杠命令或工具定义列表则按宿主自身生命周期刷新，因此更新状态会如实标记 `needs_host_reload: true`。
