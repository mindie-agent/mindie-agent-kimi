# 框架稳定性与验证

<b>中文</b> | <a href="framework-stability.en.md">English</a> · <a href="README.md">返回文档索引</a>

本文说明 `mindie-agent-kimi` 的配置持久化契约、跨平台原子配置替换机制，以及可复现的测试与基准验证流程。

---

## 1. 日常使用与配置持久化

- **统一贡献开关与跨会话持久化**：安装后的首次调用（`/mindie-agent`）引导用户完成一次贡献选择，保存在 `mindie-consent.json` 中并跨任务、分支、重启、插件更新与普通故障持久保持。社区知识共享与工具故障自动上报共用这一个持久化开关：显式选择 `contribute` 同时开启公开知识共享并启动后台故障上报 worker；选择 `disabled` 同时关闭两者。旧版仅含知识共享授权（缺少统一授权 `choice_revision` / 标记为 `needs_choice`）的记录在升级后不会自动开启故障上传，需用户显式重新确认一次后才允许上传故障报告。
- **关闭共享时零采集开销**：当贡献开关处于关闭状态时，`Stop` 阶段不执行任何对话采集或整理器调用；而领域知识检索与 `remote-dev` 远端昇腾开发工具保持随时可用。
- **三态分离与损坏配置显式报错**：本地授权已写入、后台 worker 正在运行、远端 GitHub Issue/PR 已创建三者严格区分。若配置文件损坏，系统直接作为显式故障报告，不会当作“首次使用”重新弹窗覆盖。

---

## 2. 跨平台原子配置替换

- **`os.replace()` 标准原子替换**：共享配置与状态写入统一采用临时文件写入后调用标准库 `os.replace()` 完成原子替换（遵循 `v0.0.2` 反过度防御铁律三，不维护平台专用 Win32 ACL 或 `ctypes` 重命名包装）。并发读取进程要么看到完整的旧配置文档，要么看到完整的新配置文档。
- **外部占用保护**：若外部进程锁死目标配置文件导致替换失败，更新操作保留原配置并直接向调用方返回显式错误，不会触发配置重置或后台死循环重试。

---

## 3. 可复现的预检、测试与基准流程

在仓库根目录下，使用满足 SQLite `>= 3.43.0`（支持 FTS5 与 `contentless_delete`）的 Python 解释器安装 `runtime-requirements.txt`，并配置对等仓库检出路径后即可运行全套检查：

```sh
export MINDIE_PEER_CC_REPO=/path/to/mindie-agent-cc
# Node.js 用于运行真实的 MCP 启动与传输契约测试
python3 tests/preflight.py
python3 -m unittest discover -s tests -p test_runtime_contract.py
python3 -m unittest discover -s tests/native -v
python3 -m unittest discover -s tests
```

- **预检门禁（`tests/preflight.py`）**：在运行测试套件前校验对等仓库提交与锁定的运行时依赖包完全匹配；若缺失必要依赖则直接报错退出，不静默跳过。
- **发布微基准（`benchmarks/release.json`）**：定义 `public-transcript-parsing`（指标 `public_transcript_parse`，单位 `us/op`），通过 `benchmarks/release.py` 可复现地测量本地 `wire.jsonl` 公开对话提取与非公开推理过滤延迟，并随正式 Release 发布 `benchmark-results.json`、`benchmark-results.md`。
- **验收边界分离**：自动化测试与 CPU 微基准覆盖锁定依赖、进程/文件锁与协议解析性能；原生宿主交互与常备昇腾 A2/A3 NPU 硬件执行作为独立验收项管理。
