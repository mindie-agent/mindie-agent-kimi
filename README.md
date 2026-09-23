# MindIE Agent for Kimi Code

Design inherits all nine [VAWS / MindIE Agent principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). Retiring the old runtime does not retire those principles.

Thin native Kimi plugin. Shared knowledge runtime lives in `mindie-knowledge`.
This repository owns Kimi identity, `wire.jsonl` parsing, MCP dispatch, and
the K3 organizer runner.

## Install

Requires Python 3.11+, Git and an installed, signed-in Kimi Code with native plugin support.
This is a pre-release implementation; see [acceptance status](docs/acceptance.md).

```sh
git clone https://github.com/mindie-agent/mindie-agent-kimi.git
cd mindie-agent-kimi
python3 scripts/setup.py
```

Setup builds the pinned runtime in persistent MindIE data, writes MindIE's
configuration, leaves community sharing off, and registers the model-free
update check. `--knowledge-python /absolute/path/to/python` is an optional
operator override. Use `--no-schedule` for an isolated development install.

Setup prints `native_package`. Install that exact package through the host:

```sh
python3 scripts/install_kimi_plugin.py \
  --kimi-home "${KIMI_CODE_HOME:-$HOME/.kimi-code}" \
  --plugin-root /absolute/native_package/from/setup
```

Replace the package placeholder with the exact `native_package` printed by
setup. After installation succeeds, the downloaded source can be moved or
removed; keep the persistent runtime and launchers. The helper uses Kimi's
native plugin API. It does not fabricate the host's
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

For example:

```text
/mindie-agent:sharing-enable --repository owner/repo --account USER --project-root /absolute/path --visibility public
```

With sharing off there is no Stop transcript collection, capture or organizer
model call. Public knowledge synchronization and remote-dev remain available.

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

The following selects a retained launcher using installed configuration, so it
works after the downloaded source is removed:

```sh
MINDIE_CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/mindie-agent/kimi.json"
MINDIE_LAUNCHER=$(python3 - "$MINDIE_CONFIG" <<'PYCODE'
import json, sys
from pathlib import Path
adapter = json.loads(Path(sys.argv[1]).read_text())
update = Path(adapter["state_dir"]) / "update"
current = json.loads((update / "current.json").read_text())
print(update / "launch" / (current["sha"] or "bootstrap") / "mindie_launch.py")
PYCODE
)
python3 "$MINDIE_LAUNCHER" --config "$MINDIE_CONFIG" updater status
```

Replace `status` with `check` to check main, or `uninstall-schedule` to stop
automatic checks. `install-schedule` restores scheduling. Use `recover` only
after inspecting a recorded failure; it is an explicit new attempt.

## Optional product failure reporting

Product failure reporting is a separate user choice from knowledge contribution.
It stays off until an explicit native command enables it; the setting is shared
with the other MindIE adapters.

- `/mindie-agent:reporting-status` reads the setting and reporter state.
- `/mindie-agent:reporting-enable` enables sanitized product fault reporting to
  `mindie-agent/mindie-agent` and returns an exact command to prepare the reporter.
  Run that command once outside the Hook and check the result before treating
  the reporter as ready: runtime ready and worker healthy must both be present.
  A failed preparation is not retried automatically.
- `/mindie-agent:reporting-disable` revokes future reporting; local diagnostics remain.

`not_configured` describes upload consent, not whether local logs exist.
Enabling or disabling this shared user setting affects all MindIE adapters.

Incidents contain static product stages, error types and installed code versions.
They do not collect task transcripts, prompts, commands, environment or credentials.
Cancellation, caller validation, inactive permissions, ordinary lock contention
and business-command failures are not product bug reports. The original tool
result and remote job reference stay available when a diagnostic reference is added.

Updater checks perform bounded offline log maintenance even with reporting off.
launchd output goes to the null device; updater state and bounded diagnostics carry
failure evidence. No unbounded `scheduler.log` is created. Native-host/reporting
acceptance is separate from component checks; see the acceptance documentation.

## Stop or uninstall

`/mindie-agent:deactivate` ends the current task's knowledge access.
`/mindie-agent:sharing-disable` stops contribution for the configured scope.
These do not uninstall the plugin or stop model-free updates.

Before removing the plugin, close tasks using it and run the retained launcher's
`updater uninstall-schedule`. A failed cancellation preserves the plist and
reports failure. Then remove MindIE Agent in Kimi's native Plugins UI. Keep
runtime data, receipts and old launchers while they may still be in use; do not
recursively remove shared data. Uninstalling this adapter does not disable the
shared reporter. Disable reporting separately only if that is the desired
choice across all adapters.

Windows real-machine acceptance will follow on the user's dedicated machine
after merge. Old business Skills and profiling remain deferred; a successful
install does not establish the complete contribution and Bot loop.
