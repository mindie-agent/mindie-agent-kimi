# 自动更新机制

<b>中文</b> | <a href="update.en.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-kimi` 通过操作系统定时任务选择官方仓库中与实际 `kimi --version` 兼容的最高正式 Release，排除 draft 和 prerelease，且不会降级。`mindie-plugin-release.json` 声明版本、精确源 SHA 和兼容范围；源码中的 `harness-compatibility.json` 必须与发布附件一致。当前声明支持且有实机依据的宿主版本为 `2.1.1`、`2.2.0` 与 `2.3.1`。源码镜像可由 `update_remote` / `MINDIE_KIMI_UPDATE_REMOTE` 指定，但不能改变官方 Release 的选择结果。

---

## 1. 工作原理与原子切换流程

1. **系统调度触发**：在 macOS 上通过 `launchd`（`agent.mindie.kimi-update`）每 5 分钟运行一次更新检查（Windows 计划任务 `schtasks` 代码已实现，待独立实机验收）。全程无需常驻守护进程，也不消耗任何大模型调用。
2. **非阻塞锁与独立代数构建**：单次检查受非阻塞 `check.lock` 与 1200 秒总超时（`CHECK_BUDGET = 1200.0s`）保护。更新器校验正式 Release tag 指向声明的 Commit SHA，并在安装依赖前核对取回源码中的插件版本与兼容声明，并在 `update/generations/<sha>` 下构建不可变目录：包含源码、基于 `runtime-requirements.txt`（仅允许完整 Commit SHA）独立创建的锁定虚拟环境，以及对应的 `config/` 配置。用户的准入记录、社区贡献授权与状态目录保持不变。
3. **单一原子指针（`update/current.json`）**：`current.json` 记录唯一生效的四元组 `{generation, python, adapter_config, sha}`。每次调用子进程时，启动器都会注入该四元组对应的配置与解释器，确保代码、Python 环境与配置始终来自同一个完整代数。
4. **持久化启动器路径**：每次构建生成的宿主插件清单均指向带版本哈希的启动器路径（`update/launch/<sha>/mindie_launch.py`，初始引导位于 `update/launch/bootstrap/`）。构建新版本绝不原地覆盖正在运行的入口，已加载的旧启动器路径依然可正常调用并自动转发至最新提交的代数。
5. **排他锁切换与原生回读校验**：切换阶段获取排他操作锁，先调用当前核心的 `stop_if_idle` 安全停止空闲服务，再通过 Kimi 原生插件 API 安装新插件包并回读校验 ID、版本号、启用状态与源路径，确认无误后原子翻转 `current.json`。若安装失败，自动回滚指针与原生插件包，并在状态回执中如实记录 `rollback`。
6. **共享组件正式 Release 独立自动更新（`component_update.py`）**：当没有更高版本的插件 Release 需要安装时，更新器直接从各官方组件仓库发现正式 GitHub Release，按 `max(当前已安装版本, 插件基线版本, 组件正式 Release)` 在 `update/component-runtimes/` 下构建带完整 `uv.lock` 的旁路环境，通过无模型探针并确认空闲后保留 `host-package` 以备回滚，并以 `update_native=False` 直接原子切换 `current.json`（无需重新安装 Kimi 原生插件定义）。当预备新插件 Release 时，同样先将组件推进至已安装版本下界再构建宿主包。
7. **被动检查不拉起知识服务或同步知识源**：后台更新检查（`updater.py check`）仅执行兼容版本校验、代数目录构建与空闲插件指针切换，绝不在被动后台检查中拉起知识服务或运行公共知识源同步（公共知识源同步仅在用户通过 `/mindie-agent` 显式启用知识会话后按需执行）。

---

## 2. 常用管理命令

```sh
python3 scripts/updater.py check               # 立即执行一次有界更新检查
python3 scripts/updater.py status              # 离线只读查看当前生效版本、最近结果与回滚记录
python3 scripts/updater.py recover             # 显式重试本次检查，仍执行正式发布与兼容性校验
python3 scripts/updater.py install-schedule    # 注册系统定时更新任务
python3 scripts/updater.py uninstall-schedule  # 移除系统定时更新任务（卸载插件前执行）
```

Windows 任务使用同一 Python 安装中的 `pythonw.exe` 启动 UTF-8 包装脚本，更新器及其控制台子进程在隐藏窗口中运行。已安装的 `.cmd` 任务需要重新执行 `install-schedule` 才会替换；缺少 `pythonw.exe` 时注册会明确失败，保留已有任务。

---

## 3. 容错退避与宿主重载边界

- **失败退避与防死循环**：单次检查内部不做循环重试。若遇到临时网络波动或限流，保留当前完好版本并记录 `next_retry_at`，交由后续定时检查按退避时间重试（全程保持 TLS 证书校验开启）。内容、版本、tag 或兼容声明不一致时不会安装，错误会显示在状态中。没有兼容正式版本时报告 `no_compatible_release`；当前代数与用户授权保持不变。未分类的命令失败只在本地 `failed.json` 保留一条去凭据、长度受限的 stderr 原因；共享诊断仍只写静态分类与退出码。Kimi 保持 diagnostics 0.5.3（`1c465a18...`），不降级。不可变的 0.1.2 更新器在不带 `--overrides` 的普通 `uv pip install` 下无法解析 0.5.3 与 knowledge/remote-dev 间接声明的 `801a01b...` 冲突，因此第一跳不保证自动完成，也不声称无缝迁移；一次性恢复路径是从候选发布包或源码直接运行现有的 `scripts/updater.py recover --config <现有配置>`（候选构建带 `--overrides`，并走同一原生安装/回滚事务），或在新配置目录运行 `scripts/setup.py`，之后自动更新恢复。
- **宿主重载说明（`needs_host_reload`）**：更新回执中的 `current.json` 只表示已提交的**调度代数**，不代表已经在内存中加载旧插件的宿主进程已经切换。指针切换后，稳定启动器仍按调用读取 `current.json`，因此下一次 `tools/list`/`tools/call` 与 Hook 子进程会使用新代数的启动器、解释器和脚本；已有任务授权、旧代数目录与旧启动器继续保留，正在运行的宿主/任务不会被杀死或强制重载。Kimi 宿主在启动时加载的 Skill/命令/MCP 清单与工具缓存，只会在宿主自然重启或新会话/新进程时刷新，故状态如实保留 `needs_host_reload: true`，不声称旧会话内存中的定义已变成新版本。旧 0.1.2/0.1.3 更新器生成的宿主包可能缺少 `toolTimeoutMs` 或 profile 环境变量；新代数的启动器会在发现前补上 profile，并在下一次检查时依据旧回执一次性重建/重装该宿主包，期间不触碰运行中的宿主。
