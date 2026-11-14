# mindie-agent-kimi 0.1.24

[English](mindie-agent-kimi-0.1.24.en.md)

- **Windows 无控制台 PowerShell 5.1 计划任务回读与 UTF-8 无 BOM 编码修复（`updater_schedule.py`）**：修复 `_verify_scheduled_task` 在 `CREATE_NO_WINDOW` 子进程中调用 `[Console]::OutputEncoding = [Text.UTF8Encoding]::new()` 时因缺少控制台句柄抛出 `IOException`（导致中文路径退回 OEM `CP936`）且默认构造写出 UTF-8 BOM（触发 `ET.fromstring` `ParseError`）的双重问题，改为向 `[Console]::OpenStandardOutput()` 直接写入无 BOM UTF-8 字节流并在解析前剥离前导 `\ufeff`。
- **Windows 原生进程退出等待与用户级 Git 探测增强（`with_runtime.py`、`mindie_launch.py`、`paths.py`）**：在 Windows 上将 `os.execv` / `os.execve` 替换为带 `CREATE_NO_WINDOW` 的同步 `subprocess.run` 等待，消除 Windows CRT `exec` 父进程提前退出竞态；支持 `\\?\` 扩展路径与盘符大小写无关的 `same_interpreter` 判等，并在 `GIT_WINDOWS_LOCATIONS` 中补充 `%LOCALAPPDATA%\Programs\Git` 与 Scoop 用户级 Git 安装路径。
- **WSL2 `/mnt/<drive>` 宿主二进制隔离与 DrvFS 权限兼容（`paths.py`、`organizer.py`、`setup.py`、`updater_schedule.py`、`updater_staging.py`、`install_kimi_plugin.py`、`community_config.py`、`component_tools.py`）**：新增 `in_wsl`、`is_windows_host_binary`、`which_host_binary` 与 `is_windows_or_wsl_drvfs`，避免 WSL2 `PATH` 中的 `/mnt/c/...` Windows `.exe` 或同名 shim 遮蔽 Linux 原生 CLI，并在 WSL DrvFS 挂载盘（默认 `0o777`）上兼容 `doctor_isolated` 配置权限检查。
- **版本目录选择传输异常映射与本地 HTTP 编程错误隔离（`bounded.py`、`release_metadata.py`）**：在 `release_metadata.py select` 子进程边界统一将瞬时 TLS/流中断/DNS 异常映射为静态 `CatalogError`（`temporary_network` / `certificate`），同时排除 `ImproperConnectionState` 与 `NotConnected` 等本地 HTTP 状态机编程错误。
