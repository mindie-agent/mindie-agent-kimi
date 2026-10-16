# Windows 原生启动契约

<b>中文</b> | <a href="windows-launch-contract.en.md">English</a> · <a href="README.md">返回文档索引</a>

在 Windows 环境下，Kimi Code 通过无 Shell 的子进程方式（`shell: false`）启动插件声明的 MCP 服务。本文说明 `mindie-agent-kimi` 在原生 Windows 环境下的无 Shell 启动配置与 Windows Job Object 后代进程回收机制。

---

## 1. 无 Shell 直接启动与参数传递

- **避免 `.cmd` 包装器 `EINVAL` 错误**：在 Windows 下，Node.js 的 `spawn(..., {shell: false})` 直接拉起 `.cmd` 批处理文件会触发 `EINVAL` 报错。因此，Windows 平台生成的插件包直接在 MCP 配置中声明 Python 可执行文件名（并将其所在目录置于该子进程 `PATH` 首位），将启动器脚本、配置文件与操作名作为独立参数数组传入，全程不经过 `cmd.exe` 或 PowerShell 字符串拼接，也不修改用户全局 `PATH`。
- **路径字符兼容**：支持包含中文/Unicode、空格及特殊字符的安装路径。

---

## 2. Windows Job Object 后代进程回收

- **挂起创建与先绑定后恢复**：在 Windows 上，前端进程以挂起状态（Suspended）创建辅助子进程，先将其加入带 `KILL_ON_JOB_CLOSE` 标志的 Windows Job Object，随后再恢复执行（避免子进程在绑定 Job 前抢先派生孙进程导致逃逸）。
- **可靠进程树清理**：当主进程正常退出、请求取消或发生管道超时关闭时，操作系统内核通过 Job Object 自动终止并回收全部后代子进程。

---

## 3. 运行原生边界验证

安装 `runtime-requirements.txt` 并确保 `node` 在 `PATH` 中后，可执行以下命令验证原生启动与进程回收契约：

```sh
python3 -m unittest discover -s tests/native -v
```

- **宿主参考文档**：[Kimi Code 插件命令规范](https://moonshotai.github.io/kimi-code/en/customization/plugins.html) · [MCP 子进程环境说明](https://moonshotai.github.io/kimi-code/en/customization/mcp)
