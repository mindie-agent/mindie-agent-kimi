# Windows Native Launch Contract

<a href="windows-launch-contract.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

On Windows, Kimi Code launches plugin MCP stdio servers without a shell (`shell: false`). This document describes how `mindie-agent-kimi` configures shell-free MCP launching and enforces descendant process cleanup via Windows Job Objects.

---

## 1. Shell-Free `uv` MCP Launch & `WindowsApps` Stub Rejection

- **Avoiding `.cmd` `EINVAL` and Bare `python3` Store Stubs**: Spawning a `.cmd` batch file under Node.js with `shell: false` fails on Windows with `spawn EINVAL`, while invoking bare `python3` can hit Microsoft Store 0-byte redirector stubs under `%LOCALAPPDATA%\Microsoft\WindowsApps`. `kimi.plugin.json` therefore declares `"command": "uv"` on all three MCP servers (`knowledge`, `remote`, `components`) with explicit argument arrays `["run", "--no-project", "--python", "3.13", "python", "-B", "-X", "utf8", "./scripts/mindie_launch.py", "mcp", "<surface>"]`, and runs `PreToolUse` and `Stop` hooks via `uv run --no-project --python 3.13 python -B -X utf8 ...`.
- **Fallback Interpreter Validation**: Whenever `scripts/mindie_launch.py` or `scripts/install_kimi_plugin.py` discovers a fallback Python or native binary (`PATH` scan or `uv python find ">=3.11"`), Windows candidates containing `\windowsapps\` or reporting `st_size == 0` are rejected immediately (and WSL/POSIX scans reject `.exe`/`.cmd` shims and `MZ` binaries), without mutating the user's global `PATH` and always passing `CREATE_NO_WINDOW` (`0x08000000`) on Windows subprocesses.
- **Special Character Safety**: Tested against installation paths containing Unicode characters, spaces, and shell metacharacters.

---

## 2. Descendant Process Ownership via Windows Job Objects

- **Suspended Start Before Job Assignment**: To prevent a race where a child spawns grandchildren before joining the job, the stdio frontend starts helper subprocesses in a suspended state, assigns them to a Windows Job Object configured with `KILL_ON_JOB_CLOSE`, and then resumes execution.
- **Deterministic Tree Cleanup**: Whether the leader process exits normally, is cancelled, or hits an inherited-pipe timeout, closing the Job Object handle guarantees that all child and grandchild processes are terminated.

---

## 3. Running the Native Launch Verification Suite

With the pinned runtime dependencies installed and `node` available on `PATH`, run:

```sh
uv run --no-project python -m unittest discover -s tests/native -v
```

- **Host References**: [Kimi Code Plugin Command Rules](https://moonshotai.github.io/kimi-code/en/customization/plugins.html) · [MCP Child Environment](https://moonshotai.github.io/kimi-code/en/customization/mcp)
