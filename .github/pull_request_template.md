## 变更摘要 (Summary)

- 

## 宿主插件架构核查清单 (Pre-PR Checklist — `host-plugin`)

> 请逐项核实并勾选 `[x]`；纯文档变更可在对应证据中填写 `not-applicable: docs-only`。

- [ ] **HOST-NATIVE（宿主原生无窗进程与计划任务契约）**：确认无 `CREATE_NEW_CONSOLE` / `DETACHED_PROCESS` / Windows 裸调 `os.kill(pid, 0)`，Windows 计划任务与后台子进程严格使用 `CREATE_NO_WINDOW` 及 PE `Subsystem == 2` `pythonw.exe`，核心模块 `< 800` 行且允许合法符号链接（无 `O_NOFOLLOW` 一刀切拦截）。
- [ ] **HOST-WIRE（单一语义 `mindie-agent` Skill、并发常驻 MCP 与 Receipt Honesty）**：确认收敛为单一静默语义 `mindie-agent` Skill（无多余斜杠命令刷屏、无评测条款外溢），常驻并发路由 MCP 请求（状态/只读不阻塞于前台长任务），父子会话共享 `remote` `job_id` 句柄，且主操作成功后本地回执异常保持 `isError=False` 并附 `receipt_warning` + `receipt_unconfirmed=True`。
- [ ] **HOST-ADMISSION（全表面零 `request_nonce` 默认常驻与 `Stop` 同意范围前置门禁）**：确认 `remote`、`components` 与 `knowledge` 全表面 `inputSchema.required` 不含 `request_nonce` 且默认开箱直通；写操作按需签发默认会话租约（`not_before=0.0`），`Stop` 在打开 SQLite 前校验 `mindie-consent.json` 与 `project_roots`（关闭或越界时零 SQLite 开销快速跳过）。
- [ ] **HOST-UPGRADE（`0.1.26` 在线平滑升级兼容桩、Seed 锁与双代 GC 保护）**：确认保留 `0.1.26 -> 0.1.27` 过渡路径桩（`< 30` 行）与 `update/current.json` 结构兼容，`assets/mindie-harness-seed.pyz` 与 `seed-lock.json` 一致，计划任务先迁移再回收旧代（`Active + Rollback <= 2`）。
- **自查结论与证据**：

## 验证结果 (Verification)

- 
