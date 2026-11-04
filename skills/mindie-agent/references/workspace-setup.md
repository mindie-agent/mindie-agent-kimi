# Development Workspace Setup

Run this flow during initial installation and first-use onboarding right after presenting [用户须知](user-notice.md) (including when community contribution is disabled):

1. **Pop up native onboarding questions (`AskUserQuestion`)**: When `setup.py sharing --status` reports `needs_onboarding_prompt: true` or workspace onboarding is pending, call Kimi Code's native **`AskUserQuestion`** tool (or output a structured numbered choice card `[1]/[2]` if `AskUserQuestion` is unavailable) to ask:
   - **Community Sharing & Fault Reporting**: `(Recommended) Enable community sharing & fault reporting (contribute)` vs `Keep disabled for local-only use (disabled)`.
   - **GitHub Star ⭐**: `(Recommended) Star mindie-agent/mindie-agent ⭐` vs `Skip for now`.
   - **Local Development Workspace**: Recommend [mindie-agent/vllm-ascend-workspace](https://github.com/mindie-agent/vllm-ascend-workspace.git) and offer (a) `(Recommended) Create and initialize vllm-ascend-workspace`, (b) `Keep the current directory`, or (c) `Reuse an existing local workspace directory`. Creating or reusing a workspace also registers it in the client's native project list.
2. **Verify GitHub authentication**: Before cloning or configuring forks, ensure GitHub authentication is ready via [GitHub Sign-In](github-authentication.md) using process-scoped `GIT_CONFIG_COUNT`.
3. **Clone or inspect (`WORKSPACE_ROOT`)**:
   - New directory: `git clone https://github.com/mindie-agent/vllm-ascend-workspace.git "<destination>"`.
   - Existing directory: inspect the checkout (`.agents/workspace.json` or `./vllm` + `./vllm-ascend`) and preserve local changes and configuration.
4. **Register in the native client**:
   - Kimi Web: open the workspace via the working-directory/session picker (or `POST /api/sessions/` with `{"work_dir":"<absolute-path>","create_dir":false}` on the local Kimi Web server, verifying `GET /api/work-dirs/`).
   - Kimi CLI: launch with `kimi --work-dir "<absolute-path>"`.
5. **Initialize repositories**: On approval, read the workspace's `AGENTS.md`, `README.md`, and `.agents/scripts/README.md`, then run:
   - `uv run --no-project --python 3.13 python .agents/scripts/repo_init.py --no-input` (reusing saved `workspace.json` fork settings), or
   - `uv run --no-project --python 3.13 python .agents/scripts/repo_init.py --github-user <account> --no-input` (for first-time setup with the user's forks).
6. **Apply sharing choice and optional Star in order**:
   - After resolving `WORKSPACE_ROOT`, run `uv run --no-project --python 3.13 python "$KIMI_SRC/scripts/setup.py" sharing --enable --project-root "$WORKSPACE_ROOT"` (or `--disable`).
   - If the user approved starring the repository, run `gh api --hostname github.com --silent -X PUT /user/starred/mindie-agent/mindie-agent`.
7. **Report status**: Report the workspace path, native client registration status, sharing status, and repository initialization status clearly.

Suggested prompt (in the user's language):
“是否创建并初始化 vllm-ascend-workspace，同时添加到当前客户端工作区？也可以使用已有目录，或继续当前目录。”
