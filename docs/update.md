# 自动更新机制

<b>中文</b> | <a href="update.en.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-kimi` `0.1.31` **不再内置更新器**：插件运行时只保留 5 个薄文件，更新完全委托给共享基座 [`mindie-harness`](https://github.com/mindie-agent/harness) 的 `mindie_harness.runtime.wheelhouse.update_runtime` 与 `mindie_harness.runtime.scheduler`。更新通道从官方 Release 选择与实际 `kimi --version` 兼容的正式版本，排除 draft / prerelease，且不会降级。

---

## 1. 工作原理

1. **通道发现与兼容性校验**：`update_runtime` 读取官方发布通道与 `mindie-plugin-release.json` 声明的版本、精确源 SHA 和兼容范围；兼容声明必须与源码中的 `harness-compatibility.json` 一致，当前支持 Kimi `>=2.1.1 <=2.999.999`（含预发布后缀）。
2. **独立运行时构建**：候选运行时在 `~/.local/share/mindie-agent/runtimes/kimi/` 下构建，使用 `runtime-requirements.txt` / `runtime/active-overrides.txt` 的完整 Commit SHA 与 `uv` 锁定环境；构建前后校验组件版本下界，绝不降级已安装的更高正式版本。
3. **原子激活与回滚**：候选环境通过无模型探针后，在更新锁内原子写入 `kimi.json` 的 `python` / `component_release`，不原地覆盖正在运行的入口；失败时保留旧运行时并记录可回滚回执。
4. **系统定时任务迁移**：macOS `launchd` 与 Windows 计划任务的注册、迁移和移除由 `mindie_harness.runtime.scheduler` 负责；所有包装器只使用 `CREATE_NO_WINDOW`（`0x08000000`），不产生控制台弹窗。
5. **被动检查零副作用**：后台检查只做兼容性校验、候选构建与空闲切换，不会拉起知识服务或同步公共知识源；公共知识源同步仅在会话内按需调用 `knowledge_sync`。

---

## 2. 常用管理命令

```sh
python3 scripts/setup.py update                 # 执行一次有界更新检查（发现兼容正式版本即构建并原子切换）
python3 scripts/setup.py update --force         # 忽略退避，立即重新检查
python3 scripts/setup.py update --offline       # 仅使用本地 wheelhouse，不访问网络
python3 scripts/setup.py update --background    # 由宿主以后台方式执行本次检查
python3 scripts/setup.py doctor                 # 只读核对当前运行时、组件、贡献层级与远端配置
```

也可随时调用 MCP `mindie_status` 查看当前生效运行时与组件版本；`mindie_doctor` 提供更完整的就绪检查与 `remediations`。

---

## 3. 容错退避与宿主重载边界

- **失败退避与防死循环**：临时网络或证书信任故障保留当前安装并记录 `next_retry_at`，由后续正常检查重试；TLS 校验始终开启。内容、版本、tag 或兼容性不匹配在激活前明确失败，回执只保留去凭据、有界的原因与静态类别。
- **宿主重载说明**：更新成功后，下一次 `tools/list` / `tools/call` 与 Hook 子进程会使用新运行时；Kimi 宿主启动时加载的 Skill/MCP 清单与工具缓存只会在自然重启或新会话时刷新，因此不会声称旧会话内存中的定义已切换。运行中的任务不会被强制杀死或重载。
- **旧版本迁移**：`0.1.31` 之前的 `update/current.json` 与历史代数目录保留只读；新基座以 `kimi.json` 的 `python` / `component_release` 为唯一权威，不再读取 `current.json`。如旧配置指向已删除的 `scripts/transcript.py`，`entry.py` 会在首次 `Stop` 时把 `kimi.engine.json` 的 `transcript_adapter` 自动修正为 `scripts/kimi_transcript.py`。
