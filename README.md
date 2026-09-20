# MindIE Agent for Kimi Code

Thin native Kimi plugin. Shared knowledge runtime lives in `mindie-knowledge`.
This repository owns Kimi identity, `wire.jsonl` parsing, MCP dispatch, and
the K3 organizer runner.

## Install

1. Python 3.11+ environment: `runtime-requirements.txt` (track main until release).
2. `python3 scripts/setup.py --knowledge-python /path/to/venv/bin/python`
   Headless setup leaves sharing **off**. `--community-*` also works after
   install to opt in.
3. Native plugin install: `scripts/install_kimi_plugin.py --kimi-home <isolated-or-explicit-home>`
   (Kimi `POST /api/v1/plugins`). Kimi 0.42.0 does not auto-update local-path
   plugins; reinstall through that same API. Knowledge: `mindie_knowledge.loop.cli sync`.
   `scripts/update.py` combines both. CLI `[upgrade] auto_install` updates the
   kimi binary only.

`setup.py` writes MindIE files under `~/.config/mindie-agent/` and
`~/.local/share/mindie-agent/kimi`. It does not modify `~/.kimi-code`.

## First use

`/mindie-agent:init` — native plugin_command bind, no SessionStart model launch.

## Tools

Knowledge MCP requires init and a fresh `request_nonce`. Remote-dev is
independent of knowledge activation and also requires `request_nonce`.
