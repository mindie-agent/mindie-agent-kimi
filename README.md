<p align="center">
  <img src="https://raw.githubusercontent.com/mindie-agent/mindie-agent/main/assets/brand/mindie-agent-logo.png" alt="MindIE Agent logo" width="128" height="128">
</p>

# MindIE Agent for Kimi Code

[![License: MIT](https://img.shields.io/badge/license-MIT-0F766E.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-2563EB.svg)](runtime-requirements.txt)
[![Host: Kimi Code](https://img.shields.io/badge/host-Kimi%20Code-7C3AED.svg)](kimi.plugin.json)

中文 | [English](README.en.md)

面向 **Ascend（昇腾）推理与算子工程**的 **MindIE Agent** Kimi Code 原生插件适配器，提供本地领域知识检索、隐私门控的社区经验沉淀、通用 SSH 远端开发（`remote-dev`）与结构化故障诊断。

本仓库完整继承 [MindIE Agent / VAWS 九条设计原则](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md)（旧运行时的退役不改变设计原则的有效性）。作为轻量级 Kimi 原生插件，共享知识运行时由 `mindie-knowledge` 承载，本仓库负责 Kimi 会话身份标识、`wire.jsonl` 解析、MCP 调度以及可选的 K3 元数据提取器。

> **生态与版本说明**：MindIE Agent 首个正式产品版本（`v0.0.1`）聚焦于 Codex（`mindie-agent-codex`）与 Claude Code（`mindie-agent-cc`）宿主；`mindie-agent-kimi` 作为生态中的实验性 / 源码安装（source-installed）适配器持续维护，实机验收与边界说明见[文档索引](#文档索引)。

---

## 核心特性

- **按需领域知识检索**：基于本地 SQLite FTS5（支持 `contentless_delete`）提供低延迟的昇腾推理与算子工程知识检索，辅助排障、算子迁移与性能优化，知识仅作参考且不强制预设工作流。
- **本地经验闭环与隐私门控共享**：
  - 采集路径使用宿主公开的会话投影（public transcript projection）并执行本地确定性脱敏（deterministic redaction），仅保存完整用户消息与可见助手回复，不包含工具载荷，也不使用正文改写模型。
  - 安装与更新自动配置扫描器与可选的 K3 元数据 worker（仅提取标题与摘要，禁用或最小化思考开销；提取失败时保留原文摘录，不增加用户配置负担）。
  - 社区贡献默认关闭，仅在用户显式授权公开仓库后按项目范围启用。共享范围、脱敏边界、模型服务商处理与公开 Git 历史说明见[用户须知](skills/mindie-agent/references/user-notice.md)。
  - 安装或首次配置时在展示用户须知后，可按需推荐并引导初始化 [mindie-agent/vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git) 工作区（详见[工作区配置说明](skills/mindie-agent/references/workspace-setup.md)）。
- **SSH 远端昇腾开发（`remote-dev`）**：提供独立的远端命令执行、文件同步与长任务轮询能力，无需绑定知识入口或开启社区共享即可直接使用。
- **结构化故障诊断与无模型更新**：提供与任务隔离的本地只读状态诊断、可选的脱敏产品故障上报，以及由系统调度器驱动的后台不可变代数（generation）原子更新与回滚机制。

---

## 快速开始

### 1. 环境要求与安装

需要 Python 3.11+、Git，以及已安装并登录、支持原生插件的 Kimi Code。

知识引擎的 Python 解释器需链接 **SQLite 3.43.0 及以上版本**，且编译启用了 FTS5 与 `contentless_delete` 支持。安装脚本会直接校验底层 SQLite 动态库能力（仅凭 Python 版本号不能证明满足该要求）。

```sh
git clone https://github.com/mindie-agent/mindie-agent-kimi.git
cd mindie-agent-kimi
python3 scripts/setup.py
```

`setup.py` 会在持久化 MindIE 数据目录中构建固定版本的虚拟环境（pinned runtime）、写入 MindIE 配置文件、保持社区共享默认关闭，并注册无模型的定时更新检查。可通过 `--knowledge-python /absolute/path/to/python` 显式指定解释器，或在隔离开发安装时使用 `--no-schedule` 跳过定时任务注册。

`setup.py` 执行完成后会输出 `native_package` 路径。请使用该确切路径通过宿主原生插件接口完成安装：

```sh
python3 scripts/install_kimi_plugin.py \
  --kimi-home "${KIMI_CODE_HOME:-$HOME/.kimi-code}" \
  --plugin-root /absolute/native_package/from/setup
```

请将参数中的占位路径替换为 `setup.py` 实际打印的 `native_package` 绝对路径。安装成功后，下载的源码目录可移动或删除（请保留持久化运行时与启动器目录）。安装脚本调用 Kimi 原生插件 API，不会伪造宿主的已安装插件注册表；生成的插件包指向持久化启动器，因此在后续自动更新后，已加载的入口依然可直接调用。

- **默认配置目录**：`~/.config/mindie-agent/`
- **默认本地运行时数据目录**：`~/.local/share/mindie-agent/kimi`
- 安装后仍可通过 `setup.py` 的 `--community-*` 参数修改配置，无需重装或手动编辑 JSON。

当前实现行为、可复现检查命令与验收边界详见[框架稳定性与验证说明](docs/framework-stability.md)。

### 2. 首次使用与常用命令

在 Kimi Code 会话中调用 `/mindie-agent`（或通过技能入口 `/skill:mindie-agent`、命令别名 `/mindie-agent:init` / `/init`）。入口会复用已保存的安装级选择，仅在缺少公开目标仓库、账号或项目范围时提示补充。

- **启用社区共享**：在回复中提供公开仓库（`owner/repo`）与账号，或直接运行 `/mindie-agent contribute owner/repo ACCOUNT`（或 `/mindie-agent:sharing-enable`），即可为当前项目启用公开经验贡献。已配置的经验闭环会自动处理符合条件的 `Stop` 事件。任务绑定与异步处理回执相互独立；显式禁用或历史拒绝配置在用户主动修改前保持关闭（不再提供只读或稍后决定模式）。
- **关闭共享时**：不会触发 `Stop` 会话采集、抓取或整理模型调用；公共知识同步与 `remote-dev` 远端开发仍可正常使用。

| Kimi Code 命令 | 功能说明 |
|---|---|
| `/mindie-agent`（或 `/skill:mindie-agent`、`/mindie-agent:init`、`/init`） | 绑定当前原生 Kimi 任务、确保后台知识服务就绪并返回当前配置状态 |
| `/mindie-agent:status`（或 `/status`） | 离线查询当前任务的知识配置、服务健康度及最近 5 条采集与贡献批次状态（不启动模型或初始化数据库） |
| `/mindie-agent:deactivate`（或 `/deactivate`） | 解除当前任务的知识访问绑定（保留安装级共享与故障上报设置） |
| `/mindie-agent:recover`（或 `/recover`） | 可选地按 `--batch <id>` 检查指定贡献批次状态（常规临时网络故障由后台 worker 自动恢复，无需手动执行） |
| `/mindie-agent:sharing-enable` / `/mindie-agent:sharing-disable` / `/mindie-agent:sharing-status` | 显式启用、关闭当前项目的公开社区贡献，或离线查看共享状态 |
| `/mindie-agent:reporting-status` / `/mindie-agent:reporting-enable` / `/mindie-agent:reporting-disable` | 查询、启用或关闭独立的脱敏产品故障上报 |

原生入口在内部管理任务身份与工具绑定，用户无需手动维护 nonce 或租约（lease）。`remote-dev` 远端工具集独立于知识入口与贡献设置，可随时按需调用。

---

## 自动更新

本适配器由操作系统调度器驱动（macOS 上通过 `launchd` 每 5 分钟检查一次；Windows 计划任务注册尚未实机验收），通过宿主原生插件 API 更新本地插件包。每次检查将远端 `main` 解析为唯一 commit SHA，在独立目录中构建含固定版本虚拟环境的不可变代数（immutable generation），在排他操作锁下请求共享核心执行 `stop_if_idle`，通过 Kimi 原生插件 API 安装并回读校验，最后原子切换代数指针（含回滚回执记录）。完整机制、状态/恢复命令及宿主重载边界见 [docs/update.md](docs/update.md)。

即使已删除最初下载的源码仓库，也可通过已安装的配置文件定位持久化启动器并查看更新状态：

```sh
MINDIE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/kimi.json"
MINDIE_LAUNCHER=$(python3 - "$MINDIE_CONFIG" <<'PYCODE'
import json, sys
from pathlib import Path
adapter = json.loads(Path(sys.argv[1]).read_text())
update = Path(adapter["state_dir"]) / "update"
current = json.loads((update / "current.json").read_text())
print(update / "launch" / (current["sha"] or "bootstrap") / "mindie_launch.py")
PYCODE
)
python3 "$MINDIE_LAUNCHER" --config "$MINDIE_CONFIG" updater status
```

- 将 `status` 替换为 `check` 可立即执行一次 `main` 分支更新检查；
- 替换为 `uninstall-schedule` 可停止自动定时检查，`install-schedule` 可重新注册定时任务；
- 已知的临时网络或证书信任失败会在到达记录的退避时间后由下一次定时检查自动重试，且全程保持 TLS 校验开启；
- `recover` 仅用于清除隔离（quarantined）内容或未知安装故障，常规网络抖动无需调用。

---

## 可选的产品故障上报

产品故障上报与知识社区贡献是两个完全独立的用户选项，默认保持关闭，仅在用户通过原生命令显式启用后生效，且该配置与其他 MindIE 适配器共享：

- `/mindie-agent:reporting-status`：读取共享上报开关与 reporter 运行状态。
- `/mindie-agent:reporting-enable`：开启向 `mindie-agent/mindie-agent` 提交脱敏产品故障报告，并返回一条准备 reporter 的确切命令。请在 Hook 之外执行一次该命令并核对输出：需同时满足运行时就绪（runtime ready）与 worker 健康（worker healthy）才算准备完成；准备失败不会自动重试。
- `/mindie-agent:reporting-disable`：撤销后续上报授权（本地诊断记录仍会保留）。

`not_configured` 仅表示尚未授权上传，不代表缺少本地日志。修改此共享设置会同步作用于所有 MindIE 适配器。

上报事件仅包含静态产品阶段、错误类型与已安装代码版本，**绝不收集**任务会话原文、提示词、执行命令、环境变量或凭据。取消操作、调用方参数校验、未启用权限、常规锁竞争与业务命令执行失败均不属于产品缺陷，不会触发上报。附加诊断引用时，原始工具返回结果与远端作业引用均原样保留。

即便关闭故障上报，更新检查仍会执行有界离线日志维护。`launchd` 标准输出重定向至空设备，更新状态与有界诊断文件承载故障证据，不会产生无限增长的 `scheduler.log`。

---

## 停止使用与卸载

- `/mindie-agent:deactivate`：结束当前任务的知识库访问绑定。
- `/mindie-agent:sharing-disable`：停止已配置范围的社区经验贡献。
- 上述命令不会卸载插件，也不会停止无模型自动更新。

卸载插件前，请先关闭正在使用该插件的任务，并运行持久化启动器的 `updater uninstall-schedule` 命令（若取消定时任务失败，将保留 plist 并明确报错）。随后在 Kimi 原生 Plugins 界面移除 MindIE Agent。在仍有任务或组件可能使用时，请保留运行时数据、回执与旧启动器，切勿递归删除共享数据目录。卸载本适配器不会自动关闭跨适配器共享的故障上报开关；如需全局关闭故障上报，请单独执行 `/mindie-agent:reporting-disable`。

---

## 文档索引

完整文档索引见 [docs/README.md](docs/README.md)（[English](docs/README.en.md)）：

| 文档（中文） | English Counterpart | 说明 |
|---|---|---|
| [自动更新机制](docs/update.md) | [Automatic Update](docs/update.en.md) | 后台代数构建、原子指针切换、回滚回执、命令与宿主重载边界 |
| [框架稳定性与验证](docs/framework-stability.md) | [Framework Stability](docs/framework-stability.en.md) | 日常使用契约、组件预检与测试命令、Windows 原子配置读写与验收边界 |
| [故障诊断与本地取消](docs/failure-diagnostics.md) | [Failure Diagnostics](docs/failure-diagnostics.en.md) | 只读状态诊断、取消与 EOF 进程清理边界、远端作业引用保留 |
| [整理器故障与取消证据](docs/organizer-diagnostics.md) | [Organizer Diagnostics](docs/organizer-diagnostics.en.md) | 整理器静态退出码分类、进程组生命周期管理与独立验证记录 |
| [插件更新期间的服务连续性](docs/service-continuity.md) | [Service Continuity](docs/service-continuity.en.md) | 更新事务中的知识服务停止、有界恢复、回滚交接与验证边界 |
| [测试边界与故障检测](docs/test-boundaries.md) | [Test Boundaries](docs/test-boundaries.en.md) | 跨平台 CI 检查顺序、独立故障维度与前后端传输边界测试 |
| [Windows 原生启动契约](docs/windows-launch-contract.md) | [Windows Launch Contract](docs/windows-launch-contract.en.md) | Windows 无 shell MCP 启动、子进程 Job 归属与原生边界验证 |

---

## 许可证

本项目采用 [MIT License](LICENSE) 开源。
