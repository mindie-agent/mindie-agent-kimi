# MindIE Agent for Kimi Code

Design inherits all nine [VAWS / MindIE Agent principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). Retiring the old runtime does not retire those principles.

Thin native Kimi plugin. Shared knowledge runtime lives in `mindie-knowledge`.
This repository owns Kimi identity, `wire.jsonl` parsing, MCP dispatch, and
the K3 organizer runner.

## Install

Requires Python 3.11+, Git and an installed Kimi Code with native plugin support.
This is a pre-release implementation; see [acceptance status](docs/acceptance.md).

Run `python3 scripts/setup.py`. It builds the pinned runtime, writes MindIE's
configuration, leaves community sharing off, and registers the model-free
update check. `--knowledge-python /absolute/path/to/python` is an optional
operator override. Use `--no-schedule` for an isolated development install.

Setup prints `native_package`. Install that exact package through the host:

```sh
python3 scripts/install_kimi_plugin.py --kimi-home /absolute/path/to/kimi-home --plugin-root /absolute/native_package/from/setup
```

The helper uses Kimi's native plugin API. It does not fabricate the host's
installed-plugin registry. The generated package points to the persistent
launcher, so already-loaded entrypoints remain callable across updates.

Configuration defaults to `~/.config/mindie-agent/`; local runtime data defaults
to `~/.local/share/mindie-agent/kimi`. Setup supports `--community-*` after
installation; no reinstall or hand-edited JSON is needed to opt in later.

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

This adapter updates its local-path plugin package through the native host API,
driven by the OS scheduler (launchd every
5 minutes on macOS; Windows registration unverified). Each check resolves
remote `main` to one SHA, stages an immutable generation with its own
pinned venv, asks shared core `stop_if_idle` under the exclusive operation
lock, installs through Kimi's native plugin API with readback, and
atomically flips the generation pointer — rollback receipt included.
Details, status/recovery commands, and the host-reload boundary:
[docs/update.md](docs/update.md).

## Optional product failure reporting

Product failure reporting is a separate user choice from knowledge contribution.
It stays off until an explicit native command enables it; the setting is shared
with the other MindIE adapters.

- `/mindie-agent:reporting-status` reads the setting and reporter state.
- `/mindie-agent:reporting-enable` enables sanitized product fault reporting to
  `mindie-agent/mindie-agent` and returns an exact command to prepare the reporter.
  Run that command once outside the Hook and check the result before treating
  the reporter as ready. A failed preparation is not retried automatically.
- `/mindie-agent:reporting-disable` revokes future reporting; local diagnostics remain.

Incidents contain static product stages, error types and installed code versions.
They do not collect task transcripts, prompts, commands, environment or credentials.
Cancellation, caller validation, inactive permissions, ordinary lock contention
and business-command failures are not product bug reports. The original tool
result and remote job reference stay available when a diagnostic reference is added.

Updater checks perform bounded offline log maintenance even with reporting off.
launchd output goes to the null device; updater state and bounded diagnostics carry
failure evidence. No unbounded `scheduler.log` is created. Native-host/reporting
acceptance is separate from component checks; see the acceptance documentation.
