# Windows 原生启动契约

<b>中文</b> | <a href="windows-launch-contract.en.md">English</a> · <a href="README.md">返回文档索引</a>

在 Windows 环境下，Kimi Code 通过无 Shell 的子进程方式（`shell: false`）启动插件声明的 MCP 服务。本文说明 `mindie-agent-kimi` 在原生 Windows 环境下的无 Shell 启动配置与 Windows Job Object 后代进程回收机制。

---

## 1. 无 Shell `uv` 直接启动与 `WindowsApps` 空桩拒绝

- **规避 `.cmd` `EINVAL` 与裸 `python3` 应用商店空桩**：在 Windows 下，Node.js 的 `spawn(..., {shell: false})` 直接拉起 `.cmd` 批处理文件会触发 `EINVAL` 报错，而裸调 `python3` 可能命中 Microsoft Store 在 `%LOCALAPPDATA%\Microsoft\WindowsApps` 下注册的 0 字节重定向空桩。因此，`kimi.plugin.json` 在 `knowledge`、`remote`、`components` 三个 MCP 服务中统一声明 `"command": "uv"`，并通过独立参数数组传入 `["run", "--no-project", "--python", "3.13", "python", "-B", "-X", "utf8", "./scripts/mindie_launch.py", "mcp", "<surface>"]`（`PreToolUse` 与 `Stop` Hook 同样通过 `uv run --no-project --python 3.13 python -B -X utf8 ...` 拉起）。
- **后备解释器探测拒绝 `WindowsApps` 桩与跨平台垫片**：`scripts/mindie_launch.py` 与 `scripts/install_kimi_plugin.py` 在扫描 `PATH` 或调用 `uv python find ">=3.11"` 解析后备 Python 时，在 Windows 上强制剔除包含 `\windowsapps\` 或 `st_size == 0` 的路径，在 WSL/POSIX 上剔除 `.exe`/`.cmd` 与 `MZ` 头二进制，全程不修改用户全局 `PATH`，所有后台子进程统一携带 `CREATE_NO_WINDOW`（`0x08000000`）。
- **路径字符兼容**：支持包含中文/Unicode、空格及特殊字符的安装路径。

---

## 2. Windows Job Object 后代进程回收

- **挂起创建与先绑定后恢复**：在 Windows 上，前端进程以挂起状态（Suspended）创建辅助子进程，先将其加入带 `KILL_ON_JOB_CLOSE` 标志的 Windows Job Object，随后再恢复执行（避免子进程在绑定 Job 前抢先派生孙进程导致逃逸）。
- **可靠进程树清理**：当主进程正常退出、请求取消或发生管道超时关闭时，操作系统内核通过 Job Object 自动终止并回收全部后代子进程。

---

## 3. 运行原生边界验证

安装锁定运行时依赖并确保 `node` 在 `PATH` 中后，可执行以下命令验证原生启动与进程回收契约：

```sh
uv run --no-project python -m unittest discover -s tests/native -v
```

- **宿主参考文档**：[Kimi Code 插件命令规范](https://moonshotai.github.io/kimi-code/en/customization/plugins.html) · [MCP 子进程环境说明](https://moonshotai.github.io/kimi-code/en/customization/mcp)
