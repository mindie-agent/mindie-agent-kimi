# MindIE Agent for Kimi Code

Thin native Kimi plugin. Shared knowledge runtime lives in `mindie-knowledge`.
This repository owns Kimi identity, `wire.jsonl` parsing, MCP dispatch, and
the K3 organizer runner.

## Install

1. Python 3.11+ environment: `runtime-requirements.txt` (pinned to the current
   core candidate commit; root may replace the pin before release).
2. `python3 scripts/setup.py --knowledge-python /path/to/venv/bin/python`
   Headless setup leaves sharing **off**. `--community-*` also works after
   install to opt in.
3. Native plugin install: `scripts/install_kimi_plugin.py --kimi-home <isolated-or-explicit-home>`
   (`POST /api/v1/plugins`). Manifest MCP/hooks use `python3` plus script
   arguments so Windows does not depend on a shebang.

`setup.py` writes MindIE files under `~/.config/mindie-agent/` and
`~/.local/share/mindie-agent/kimi`. It does not modify `~/.kimi-code`.

## First use

`/mindie-agent:init` — one `mindie_entry` MCP call bound by PreToolUse nonce
and the current turn-opening `plugin_command` origin. No SessionStart model
launch. Unconfigured status still works offline (sharing off, no service).

Three choices: recommended public contribution, read-only, or later. Reply
`read-only`/`later`, or run `/mindie-agent:init read-only|later`. Enabling
contribution requires explicit repository, account, project root, and
`--visibility public`.

## Tools

Knowledge MCP requires init and a fresh `request_nonce`. Remote-dev is
independent of knowledge activation and also requires `request_nonce`.

## Updates

Kimi 0.42.0 does not automatically update local-path plugins, so this
adapter ships one small updater driven by the OS scheduler (launchd every
5 minutes on macOS; Windows registration unverified). Each check resolves
remote `main` to one SHA, stages an immutable generation with its own
pinned venv, asks shared core `stop_if_idle` under the exclusive operation
lock, installs through Kimi's native plugin API with readback, and
atomically flips the generation pointer — rollback receipt included.
Details, status/recovery commands, and the host-reload boundary:
[docs/update.md](docs/update.md).
