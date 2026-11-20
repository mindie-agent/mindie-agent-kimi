# Contributing to `mindie-agent-kimi`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

This repository owns the thin Kimi Code host plugin (`kimi.plugin.json`, native `wire.jsonl` transcript projection, hook bridge, and live generation handoff), backed by the shared [`mindie-agent/harness`](https://github.com/mindie-agent/harness) (`mindie_harness`) substrate.

## 1. Workspace & Line-Ending Rules

1. Develop on isolated topic worktrees (`../.worktrees/mindie-agent-kimi-<topic>`) rather than mixing changes in the base checkout.
2. Keep Unix `LF` (`\n`) line endings across all text files (`*.pyz` binary zipapps are marked `binary` in `.gitattributes`).
3. Keep `SKILL.md` and `references/*.md` free of benchmark-only schemas (`mindie-bench-ablation/1`) or mandatory `PROGRESS.md` rituals.

## 2. Pre-PR Checklist (`host-plugin`)

Every Pull Request must complete the four `host-plugin` checklist IDs in [`.github/pull_request_template.md`](.github/pull_request_template.md):

- **`HOST-NATIVE`**: Zero Windows console popups (`CREATE_NO_WINDOW`, no `CREATE_NEW_CONSOLE` / `DETACHED_PROCESS`), `uv`-backed `kimi.plugin.json` commands, and explicit `WindowsApps` 0-byte stub rejection.
- **`HOST-WIRE`**: Single semantic `mindie-agent` skill, concurrent resident MCP dispatch (`serverInfo.version == "0.1.33"`), profile-shared `remote` job store across subagents, and Receipt Honesty (`receipt_warning` + `receipt_unconfirmed=True`).
- **`HOST-ADMISSION`**: Zero required `request_nonce` across `remote`, `components`, and default-enabled `knowledge_*` tools; consent/scope check before SQLite in `Stop` followed by `run_stop_capture` (`turn` vs deterministic `notification`, preserving `inactive` revocation).
- **`HOST-UPGRADE`**: Validated `kimi.json` precedence over stale `update/current.json`, `scripts/transcript.py` compatibility shim, 9-domain content-addressed knowledge bindings under `.kimi.json.update.lock`, `assets/mindie-harness-seed.pyz` lock verification, and safe `MindIEKimiUpdate` scheduled-task retirement.

## 3. Local Verification

```bash
uv run --no-project python .github/scripts/check_lf_eol.py
uv run pytest -q -o "pythonpath=tests scripts" tests
uv run --no-project python -m unittest discover -s tests/native -v
uv run --no-project python scripts/setup.py release-metadata --source . --tests tests --revision "$(git rev-parse HEAD)"
uv run --no-project python .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
