# 自动更新机制

<b>中文</b> | <a href="update.en.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-kimi` `0.1.33` **不再内置独立更新器**：插件运行时仅保留 6 个标准薄文件，更新完全委托给共享基座 [`mindie-harness`](https://github.com/mindie-agent/harness)（`>= 0.1.5`）的 `mindie_harness.runtime.wheelhouse.update_runtime` 与 `mindie_harness.runtime.scheduler`。更新通道从官方 Release 选择与实际 `kimi --version` 兼容的最高正式版本，排除 draft / prerelease，且绝不降级。

---

## 1. 工作原理

1. **通道发现与兼容性校验**：`update_runtime` 读取官方发布通道与 `mindie-plugin-release.json` 声明的版本、精确源 SHA 和兼容范围；兼容声明必须与源码中的 `harness-compatibility.json` 一致，当前支持 Kimi `>=2.1.1 <=2.999.999`（含预发布后缀）。
2. **独立 9 组件运行时构建**：候选运行时在 `runtimes/kimi/` 下隔离构建，对齐根级 `pyproject.toml`、`uv.lock`、`product-contract.json` 与 `runtime-requirements.txt` / `runtime/active-overrides.txt` 中的全部 9 个运行时组件（含 `mindie-harness >= 0.1.5` 与 `npu-top 0.3.5`）；构建前后校验组件版本下界，绝不降级已安装的更高正式版本。
3. **原子激活、9 域引擎绑定与回滚**：候选环境通过无模型探针后，在 `.kimi.json.update.lock` 排他锁内调用 `ensure_knowledge_binding(..., already_locked=True)` 生成内容寻址 9 域 `engines/kimi/<20hex>/<domain>.engine.json` 并原子写入 `kimi.json` 的 `python`、`component_release` 与 `domain_configs`；失败时保留旧运行时并记录可回滚回执。
4. **遗留计划任务安全退役**：`setup.py init` 与 `setup.py update` 在清理旧版 `.pyw`/`.xml` 包装文件前，先无窗查询并安全删除遗留的 `MindIEKimiUpdate` Windows 计划任务（以及 macOS `launchd` `agent.mindie.kimi-update` / Linux `systemd` `mindie-kimi-update`），新任务统一由 `mindie_harness.runtime.scheduler` 托管且全程使用 `CREATE_NO_WINDOW`。
5. **被动检查零副作用**：后台检查只做兼容性校验、候选构建与空闲切换，不会拉起知识服务或同步公共知识源；公共知识源同步仅在会话内按需调用 `knowledge_sync`。

---

## 2. 常用管理命令

```sh
uv run --no-project python scripts/setup.py update                 # 执行一次有界更新检查（发现兼容正式版本即构建并原子切换）
uv run --no-project python scripts/setup.py update --force         # 忽略退避，立即重新检查
uv run --no-project python scripts/setup.py update --offline       # 仅使用本地 wheelhouse，不访问网络
uv run --no-project python scripts/setup.py update --background    # 由宿主以后台方式执行本次检查
uv run --no-project python scripts/setup.py doctor                 # 只读核对当前运行时、组件、贡献层级与远端配置
```

也可随时调用 MCP `mindie_status` 查看当前生效运行时与组件版本；`mindie_doctor` 提供更完整的就绪检查与 `remediations`。

---

## 3. 容错退避与宿主重载边界

- **失败退避与防死循环**：临时网络或证书信任故障保留当前安装并记录 `next_retry_at`，由后续正常检查重试；TLS 校验始终开启。内容、版本、tag 或兼容性不匹配在激活前明确失败，回执只保留去凭据、有界的原因与静态类别。
- **宿主重载说明**：更新成功后，下一次 `tools/list` / `tools/call` 与 Hook 子进程会使用新运行时；Kimi 宿主启动时加载的 Skill/MCP 清单与工具缓存只会在自然重启或新会话时刷新，因此不会声称旧会话内存中的定义已切换。运行中的任务不会被强制杀死或重载。
- **已验证 `kimi.json` 优先与旧版兼容回退**：当已验证的权威 `kimi.json`（`component_release/runtime.json` 为 `validated` 或 `python` 可用）与遗留 `update/current.json` 同时存在时，`mindie_launch.py` 始终优先采用 `kimi.json`，防止陈旧 `current.json` 覆盖新版 wheelhouse 升级；仅当 `kimi.json` 不存在或其解释器缺失时，才回退读取 `update/current.json`，并保留薄转发桩 `scripts/transcript.py` 兼容 `v0.1.30` 在线升级探针。
