# Windows Native Launch Contract

<a href="windows-launch-contract.md">中文</a> | <b>English</b> · <a href="README.en.md">Documentation Index</a>

On Windows, Kimi Code launches plugin MCP stdio servers without a shell (`shell: false`). This document describes how `mindie-agent-kimi` configures shell-free MCP launching and enforces descendant process cleanup via Windows Job Objects.

---

## 1. Shell-Free MCP Launch on Windows

- **Direct Executable Dispatch (No `.cmd` Wrapper)**: Spawning a `.cmd` batch file under Node.js with `shell: false` fails on Windows with `spawn EINVAL`. To ensure reliable startup without modifying the user's global `PATH`, Windows packages declare the pinned Python executable basename with its directory prepended to the MCP server's child `PATH`, passing the launcher script, config path, and operation as separate argv elements.
- **Special Character Safety**: Tested against installation paths containing Unicode characters, spaces, and shell metacharacters.

---

## 2. Descendant Process Ownership via Windows Job Objects

- **Suspended Start Before Job Assignment**: To prevent a race where a child spawns grandchildren before joining the job, the stdio frontend starts helper subprocesses in a suspended state, assigns them to a Windows Job Object configured with `KILL_ON_JOB_CLOSE`, and then resumes execution.
- **Deterministic Tree Cleanup**: Whether the leader process exits normally, is cancelled, or hits an inherited-pipe timeout, closing the Job Object handle guarantees that all child and grandchild processes are terminated.

---

## 3. Running the Native Launch Verification Suite

With `runtime-requirements.txt` installed and `node` available on `PATH`, run:

```sh
python3 -m unittest discover -s tests/native -v
```

- **Host References**: [Kimi Code Plugin Command Rules](https://moonshotai.github.io/kimi-code/en/customization/plugins.html) · [MCP Child Environment](https://moonshotai.github.io/kimi-code/en/customization/mcp)
