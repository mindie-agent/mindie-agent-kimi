# 框架稳定性与验证

<b>中文</b> | <a href="framework-stability.en.md">English</a> · <a href="README.md">返回文档索引</a>

本文说明 `mindie-agent-kimi` 的日常无感使用契约、跨平台原子配置读写机制，以及可复现的测试预检流程。

---

## 1. 日常使用与配置持久化

- **一次配置，跨会话持久生效**：安装后的首次调用（`/mindie-agent`）引导用户完成一次社区贡献选择；该选择跨任务、分支、重启、插件更新与普通故障持久保持，后续使用无需重复激活或续期。自动故障上报拥有独立的持久化开关，不随社区贡献隐式开启。
- **关闭共享时零采集开销**：当社区贡献处于关闭状态时，`Stop` 阶段不执行任何对话采集或整理器调用；而领域知识检索与 `remote-dev` 远端昇腾开发工具保持随时可用。
- **损坏配置显式报错**：短暂的服务或网络波动由后台 Worker 自动恢复；若配置文件损坏，系统直接作为显式故障报告，绝不会当成“首次使用”重新弹窗覆盖。

---

## 2. Windows 平台原子配置读写

- **删除共享读取 + 原生原子重命名**：共享配置存储结合了删除共享（Delete-Sharing）读取与 Windows 原生原子重命名语义（[`FILE_RENAME_INFORMATION`](https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/ntifs/ns-ntifs-_file_rename_information)）。并发读取进程要么看到完整的旧配置文档，要么看到完整的新配置文档。
- **外部占用保护**：若外部程序未开启删除共享而锁死文件，更新操作会保留原配置并明确报错，不会触发重置或死循环重试。

---

## 3. 可复现的预检与测试流程

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
- **验收边界分离**：自动化测试覆盖锁定依赖、真实进程/文件锁与协议边界；原生宿主交互与远端昇腾 NPU 硬件执行作为独立验收项管理。
