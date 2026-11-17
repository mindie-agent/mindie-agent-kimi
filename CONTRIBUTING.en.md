# Contributing to `mindie-agent-kimi`

中文版：[`CONTRIBUTING.md`](CONTRIBUTING.md)

This repository owns the thin Kimi Code host plugin (`kimi.plugin.json`, native `wire.jsonl` transcript projection, hook bridge, and live generation handoff), backed by the shared [`mindie-agent/harness`](https://github.com/mindie-agent/harness) (`mindie_harness`) substrate.

## 1. Workspace & Line-Ending Rules

1. Develop on isolated topic worktrees (`../.worktrees/mindie-agent-kimi-<topic>`) rather than mixing changes in the base checkout.
2. Keep Unix `LF` (`\n`) line endings across all text files (`*.pyz` binary zipapps are marked `binary` in `.gitattributes`).
3. Keep `SKILL.md` and `references/*.md` free of benchmark-only schemas (`mindie-bench-ablation/1`) or mandatory `PROGRESS.md` rituals.

## 2. Pre-PR Checklist (`host-plugin`)

Every Pull Request must complete the four `host-plugin` checklist IDs in [`.github/pull_request_template.md`](.github/pull_request_template.md):

- **`HOST-NATIVE`**: Zero Windows console popups (`CREATE_NO_WINDOW`, no `CREATE_NEW_CONSOLE` / `DETACHED_PROCESS`, verified PE `Subsystem == 2` `pythonw.exe` for `MindIEKimiUpdate`).
- **`HOST-WIRE`**: Single `/mindie-agent` entry, concurrent resident MCP dispatch, profile-shared `remote` job store across subagents, and Receipt Honesty (`receipt_warning` + `receipt_unconfirmed=True`).
- **`HOST-ADMISSION`**: `P2.7` zero required `request_nonce` on deterministic tools; `P2.1` unactivated hiding and strict per-session nonce/lease admission on `knowledge_*`.
- **`HOST-UPGRADE`**: `0.1.26 -> 0.1.27` transition shims (`< 30` LOC), `update/current.json` compatibility, `assets/mindie-harness-seed.pyz` lock match, and Scheduled Task migration before GC.

## 3. Local Verification

```bash
python3 .github/scripts/check_lf_eol.py
python3 -m pytest -q -o "pythonpath=tests scripts" tests
python3 .github/scripts/check_pr_checklist.py --profile host-plugin --body-file <pr_body.md>
```
