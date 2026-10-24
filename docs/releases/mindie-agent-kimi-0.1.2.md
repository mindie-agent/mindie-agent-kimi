# mindie-agent-kimi 0.1.2 Release Notes

**中文** | [English](https://github.com/mindie-agent/mindie-agent-kimi/blob/v0.1.2/docs/releases/mindie-agent-kimi-0.1.2.en.md)

`mindie-agent-kimi 0.1.2` 是面向 Kimi Code 的维护更新版本（配套主产品 `mindie-agent 0.1.1`，保留既有 `0.1.1` 插件标签不变），新增共享运行时组件的独立正式 Release 自动更新与纯组件热切换能力，适配 Kimi `2.3.1` 原生用户入口格式，并收敛按会话手动开启 `/mindie-agent` 知识循环的边界。

## 核心更新

- **共享组件独立自动更新与免重装原生插件切换**：
  - 当没有更高版本的插件 Release 需要安装时，更新器直接从各官方组件仓库发现正式 GitHub Release，按“当前已安装版本、插件基线版本、组件最新正式 Release”三者最大值在旁路隔离目录构建带完整 `uv.lock` 的组件运行环境（单次检查总预算 `CHECK_BUDGET = 1200.0s`，检查间隔 `INTERVAL_SECONDS = 300`）。
  - 纯组件更新在通过无模型运行时探针且确认后台服务空闲后，直接原子切换 `current.json` 运行时指针并保留既有 `host-package` 以备回滚，**无需重新安装 Kimi 原生插件定义**（`update_native=False`）。
  - 当预备新版本插件 Release 时，自动继承当前已安装组件的版本下界，防止插件升级回退已独立更新的组件。
- **Kimi `2.3.1` 兼容声明与当前回合原生用户 `/mindie-agent` 入口识别（`scripts/identity.py`、`harness-compatibility.json`）**：
  - `harness-compatibility.json` 新增声明支持 Kimi `2.3.1`（与 `2.1.1`、`2.2.0` 并列）。
  - 针对 Kimi `2.3.1` 原生将用户输入的 `/mindie-agent` 保留在 `turn.prompt` / `context.append_message` 用户消息中而不展开 Skill 的情形，`scripts/identity.py` 校验当前回合起始记录为原生用户输入（`kind == "user"`）且携带有效 `promptId` 后，授予单次 `init` 入口（`activation_id="user-entry:<promptId>"`），严禁从模型参数、句中提及或跨会话继承文本派生权限。
- **探索上下文原样透传与会话显式授权边界**：
  - 知识检索的探索载荷直接透传 `mindie-knowledge` 引擎输出，适配层不再二次剥离或改写；后台更新检查绝不触发公共知识流同步、不自动授予会话知识权限，也不改变已保存的用户贡献同意，会话知识能力仍仅在当前会话手动输入 `/mindie-agent` 后生效。

## 运行时组件与验证范围

- **配套运行时组件**：锁定正式发布的 `mindie-knowledge 0.13.3`（`38a42f6f778cfa555d8918fdcacd12cee4a181f5`）、`remote-dev 0.10.6`（`0463a13fce9618ec9a51904869d61acb0cdb8538`）与 `mindie-diagnostics 0.5.2`（`801a01b2789aed7f58e971a909205a7d40b61077`）；插件基线修订与兼容的 Kimi Code 版本范围（`2.1.1`、`2.2.0`、`2.3.1`）以随包发布的 `runtime-requirements.txt` 与 `harness-compatibility.json` 为准（旁路组件候选环境在隔离构建时生成各自的 `uv.lock`）。
- **验证与验收层级**：
  1. **macOS `arm64` 原生候选实测（Kimi `2.2.0` 与 `2.3.1`）**：
     - 在 Kimi `2.2.0` 上实测验证：默认新会话直接调用 `knowledge_query` 在执行前被拒绝、不初始化知识服务且不重试；显式输入 `/mindie-agent` 激活 `entry_enabled=1`，同会话续跑继承，独立新会话不继承；默认 `remote_job_status` 直接走确定性远端路径（查询不存在的 `job_id` 正常返回调用方未找到错误，不受知识门禁阻塞）；独立组件更新对齐 `mindie-knowledge 0.13.3`、`remote-dev 0.10.6` 与 `mindie-diagnostics 0.5.2` 并保留原生插件包。
     - 在 Kimi `2.3.1` 上已完成新隔离环境原生 `setup.py` + `NativePluginAPI` 安装与完整会话闭环实测：真实用户当前回合起始输入 `/mindie-agent`（携带原生 `promptId`）单次执行 `init` 并绑定真实原生 `session_id`（`entry_enabled=1`），后台知识服务保持 `not-requested` 且不触发经验采集或外发共享；同一会话续跑（`CONTINUED`）正常继承，独立新建会话以及从已授权会话 `fork` 复制出的会话调用知识工具均被拒绝且不新增任何租约记录，SQLite 授权库中仅保留手动激活的那 1 条真实会话记录。
  2. **自动化测试与各 OS CI**：更新器测试套件、纯组件切换测试与 Kimi `2.3.1` 原生用户入口身份校验测试全部通过，跨平台契约由各 OS CI 流水线覆盖。
  3. **正式发布后回读待办**：待四端 `0.1.2` 正式 GitHub Release 发布后，执行面向已发布正式包的本地自动更新回读确认。
