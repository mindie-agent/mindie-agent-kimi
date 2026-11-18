# 贡献指南 (Contributing to `mindie-agent-kimi`)

English version: [`CONTRIBUTING.en.md`](CONTRIBUTING.en.md)

本仓库承载 Kimi Code 薄宿主插件（`kimi.plugin.json`、原生 `wire.jsonl` 转写、Hook 桥接与平滑双代切流），底层复用 [`mindie-agent/harness`](https://github.com/mindie-agent/harness)（`mindie_harness`）公共运行时底座。

## 1. 本地开发与隔离分支规范

1. 在平铺工作区下使用独立 Git Worktree 开发（例如 `../.worktrees/mindie-agent-kimi-<topic>`），禁止直接在主检出目录混改多主题。
2. 所有文本文件强制使用 Unix `LF`（`\n`）换行符（`.pyz` 等二进制资产已在 `.gitattributes` 显式声明 `binary`）。
3. 严禁在生产插件 `SKILL.md` / `references/*.md` 中写入 `mindie-bench` 专属打榜契约或强制 `PROGRESS.md` 仪式条款。

## 2. 提交 PR 前必检项 (`host-plugin`)

提交 Pull Request 前，请逐项核验并完成 [`.github/pull_request_template.md`](.github/pull_request_template.md) 中的四项机器校验 ID：

- **`HOST-NATIVE`**：Windows 无窗进程监管（禁绝 `CREATE_NEW_CONSOLE` / `DETACHED_PROCESS`，计划任务校验 PE `Subsystem == 2` `pythonw.exe`）。
- **`HOST-WIRE`**：单一语义 `mindie-agent` Skill 入口、并发常驻 MCP 派发、父子会话共享 `remote` 作业句柄（`job_id`），以及主操作成功后的 Receipt Honesty。
- **`HOST-ADMISSION`**：`remote`、`components` 与 `knowledge` 全表面零必填 `request_nonce` 且默认开箱可用；按需自动补齐默认会话租约（`not_before=0.0`），`Stop` Hook 在打开 SQLite 前校验 `mindie-consent.json` 与 `project_roots`。
- **`HOST-UPGRADE`**：保留 `0.1.26 -> 0.1.27` 过渡桩文件、`update/current.json` 兼容结构与 `assets/mindie-harness-seed.pyz` 锁校验，计划任务先迁移再执行旧代 GC。

## 3. 本地验证命令

```bash
python3 .github/scripts/check_lf_eol.py
python3 -m pytest -q -o "pythonpath=tests scripts" tests
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
