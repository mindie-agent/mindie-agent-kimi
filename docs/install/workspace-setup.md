# Development Workspace & First-Use Onboarding

Run this flow during installation and first use, right after presenting [用户须知](user-notice.md) (including when community contribution stays local-only):

1. **Install (one command; `gh` is optional and never a blocker)**:
   ```sh
   uv run https://github.com/mindie-agent/mindie-agent/releases/latest/download/install.py --host kimi
   ```
   `gh` only enables optional public knowledge PRs and sanitized fault reporting. If it is missing, signed out, or blocked by an enterprise network, choose `local-only` and continue; see [GitHub Sign-In](github-authentication.md) only when login is actually needed.
2. **Check readiness with `mindie_doctor`**: call the `mindie_doctor` MCP tool and review `runtime`, `github_channel`, and `experience_and_privacy`. Treat `overall_status: attention_needed` as guidance, not a blocker: apply the returned `remediations`, then continue the task.
3. **Pop up native onboarding questions (`AskUserQuestion`) in one call, in the user's language** (or output a structured numbered choice card if `AskUserQuestion` is unavailable):
   - **Local Development Workspace**: Recommend [mindie-agent/vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git) and offer (a) `(Recommended) 新建并初始化 vllm-ascend-workspace` / `(Recommended) Create and initialize vllm-ascend-workspace`, (b) `保持当前目录不变` / `Keep the current directory`, or (c) `复用本机已有工作区目录` / `Reuse an existing local workspace directory`. Creating or reusing a workspace also registers it in the client's native project list.
   - **Community Contribution Tier (dynamic recommendation)**: if `gh api --hostname github.com user --jq .login` succeeds, recommend `(Recommended) 开启社区共享与故障上报 (contribute)` / `(Recommended) Enable community sharing & fault reporting (contribute)`; otherwise recommend `仅本地采集 (local-only)` / `(Recommended) Local-only capture (local-only)` and offer `关闭 (disabled)` / `Disabled (disabled)` as the alternative.
   - **One-step SSH NPU Connection (optional)**: offer `连接远端昇腾 NPU` / `Connect a remote Ascend NPU` versus `跳过` / `Skip`. When connecting, collect `host`, `port`, `user`, and an optional container name; in terminal mode finish with `--password` so the user types the secret at a hidden prompt: `remote-dev auth setup --host <host> --port <port> --user <user> --password`, then verify with `remote-dev auth status` (or call the MCP `remote_auth` tool with `{"action": "setup", ...}`).
4. **Clone or inspect (`WORKSPACE_ROOT`)**:
   - New directory: `git clone https://github.com/mindie-agent/vllm-ascend-workspace.git "<destination>"`.
   - Existing directory: inspect the checkout (`.agents/workspace.json` or `./vllm` + `./vllm-ascend`) and preserve local changes and configuration.
5. **Register in the native client**:
   - Kimi Web: open the workspace via the working-directory/session picker (or `POST /api/sessions/` with `{"work_dir":"<absolute-path>","create_dir":false}` on the local Kimi Web server, verifying `GET /api/work-dirs/`).
   - Kimi CLI: launch with `kimi --work-dir "<absolute-path>"`.
6. **Initialize repositories**: On approval, read the workspace's `AGENTS.md`, `README.md`, and `.agents/scripts/README.md`, then run:
   - `uv run --no-project --python 3.13 python .agents/scripts/repo_init.py --no-input` (reusing saved `workspace.json` fork settings), or
   - `uv run --no-project --python 3.13 python .agents/scripts/repo_init.py --github-user <account> --no-input` (for first-time setup with the user's forks).
7. **Apply the contribution tier in order** (from an installed plugin directory):
   - `contribute`: `python scripts/setup.py sharing --enable --project-root "$WORKSPACE_ROOT"`
   - `local-only`: `python scripts/setup.py sharing --local-only --project-root "$WORKSPACE_ROOT"`
   - `disabled`: `python scripts/setup.py sharing --disable`
   - Verify read-only with `python scripts/setup.py sharing --status`.
8. **Report and close politely**: report the workspace path, native client registration, `mindie_doctor` status, contribution tier, and optional SSH result, then close with one short line: `⭐ If MindIE Agent helps you, a Star at https://github.com/mindie-agent/mindie-agent means a lot.` Never repeat or nag.

Suggested prompt (in the user's language):
“是否创建并初始化 vllm-ascend-workspace，同时添加到当前客户端工作区？也可以使用已有目录，或继续当前目录。”
