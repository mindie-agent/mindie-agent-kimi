# 测试边界与故障检测

<b>中文</b> | <a href="test-boundaries.en.md">English</a> · <a href="README.md">返回文档索引</a>

`mindie-agent-kimi` `0.1.31` 在受支持平台上按确定性顺序运行薄插件测试套件，确保最早拦截行尾、契约、原生启动或发布元数据异常，避免掩盖跨平台边界问题。

---

## 1. CI 执行顺序

同一测试作业内按以下顺序依次执行（前序门禁失败立即终止，避免级联报错）：

1. **行尾与语法门禁**：`python .github/scripts/check_lf_eol.py` 拒绝 CRLF；`python -m compileall -q scripts tests` 拒绝语法错误。
2. **薄插件契约（`tests/test_thin_plugin_contract.py`）**：`scripts/` 仅保留 5 个文件、合计 ≤ 1,000 行、版本 `0.1.31`、`REMOTE_DEV_HARNESS_PROFILE == "kimi"`、`entry.verify_bundled_seed()` 通过、`KimiHostAdapter` 满足 `mindie_harness.api.HostAdapter`。
3. **发布元数据（`scripts/setup.py release-metadata --source . --tests tests --revision <sha>`）**：`harness_name="kimi-code"` 的兼容声明、AST `code_paths`、`test_ids` 与 `mindie-plugin-release/1` 输出。
4. **真实 Node.js MCP 启动（`tests/native`）**：`child_process.spawn`（`windowsHide: true`、`shell: false`）按 `kimi.plugin.json` 精确参数拉起三个 MCP 服务，验证 `initialize`（`0.1.31`）、`tools/list`、JSON-RPC `-32700` 解析错误与 `ping` 恢复。
5. **完整契约套件（`pytest -o "pythonpath=tests scripts" tests`）**：覆盖冷启动 Hook 快速路径、目录范围、MCP 工具面隔离、`setup.py` 子命令、安装器资源校验与 `wire.jsonl` 解析。

---

## 2. 独立故障维度

| 测试边界 | 实际断言观测对象 | 允许使用夹具替代的部分 |
|---|---|---|
| **薄插件规模与版本** | 真实 `scripts/` 文件集合、`wc -l` 行数与 `kimi.plugin.json` 版本 | 无 |
| **种子完整性** | 真实 `assets/mindie-harness-seed.pyz` 的 SHA-256 与字节数 | 无 |
| **冷启动 Hook 范围** | 真实 `mindie_launch.py` 子进程的退出码、`{}` stdout 与零 SQLite | 授权目录使用临时目录 |
| **原生清单启动** | Node.js 以精确参数真实拉起 `python3 ./scripts/mindie_launch.py` | 使用当前 Python 解释器（`MINDIE_TEST_PYTHON_CMD` / PATH shim） |
| **MCP 工具面隔离** | `tools/list` 精确集合与跨面 `tools/call` 的 `unknown_operation` | 无 |
| **发布元数据** | `mindie-plugin-release/1` 输出与 AST 目标存在性 | `--revision` 使用固定 40-hex 测试值 |
