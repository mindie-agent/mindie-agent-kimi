# mindie-agent-kimi 0.1.2 Release Notes

**中文** | [English](https://github.com/mindie-agent/mindie-agent-kimi/blob/v0.1.2/docs/releases/mindie-agent-kimi-0.1.2.en.md)

`mindie-agent-kimi 0.1.2` 是面向 Kimi Code 的维护更新版本（配套主产品 `mindie-agent 0.1.1`，保留既有 `0.1.1` 插件标签不变），新增共享运行时组件的独立正式 Release 自动更新与纯组件热切换能力，并收敛按会话手动开启 `/mindie-agent` 知识循环的边界。

## 核心更新

- **共享组件独立自动更新与免重装原生插件切换**：
  - 当没有更高版本的插件 Release 需要安装时，更新器直接从各官方组件仓库发现正式 GitHub Release，按“当前已安装版本、插件基线版本、组件最新正式 Release”三者最大值在旁路隔离目录构建带完整 `uv.lock` 的组件运行环境。
  - 纯组件更新在通过无模型运行时探针且确认后台服务空闲后，直接原子切换 `current.json` 运行时指针并保留既有 `host-package` 以备回滚，**无需重新安装 Kimi 原生插件定义**（`update_native=False`）。
  - 当预备新版本插件 Release 时，自动继承当前已安装组件的版本下界，防止插件升级回退已独立更新的组件。
- **探索上下文原样透传与会话显式授权边界**：
  - 知识检索的探索载荷直接透传 `mindie-knowledge` 引擎输出，适配层不再二次剥离或改写；后台更新检查绝不触发公共知识流同步、不自动授予会话知识权限，也不改变已保存的用户贡献同意，会话知识能力仍仅在当前会话手动输入 `/mindie-agent` 后生效。

## 运行时组件与验证范围

- **配套运行时组件**：锁定正式发布的 `mindie-knowledge 0.13.3`、`remote-dev 0.10.6`（`0463a13fce9618ec9a51904869d61acb0cdb8538`）与 `mindie-diagnostics 0.5.2`（`801a01b2789aed7f58e971a909205a7d40b61077`）；插件基线修订与兼容的 Kimi Code 版本范围以随包发布的 `runtime-requirements.txt` 与 `harness-compatibility.json` 为准（旁路组件候选环境在隔离构建时生成各自的 `uv.lock`）。
- **验证与验收边界**：既有更新器测试套件（`20 passed`）与新增纯组件切换测试（`2 passed`）全部通过。跨四端宿主的正式包升级与原生新会话端到端联合验收随主产品发布流程闭环。
