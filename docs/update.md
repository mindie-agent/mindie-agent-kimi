# MindIE Kimi automatic update

Tracks `main` of the plugin remote (default
`https://github.com/mindie-agent/mindie-agent-kimi.git`, override with
`update_remote` in the adapter config or `MINDIE_KIMI_UPDATE_REMOTE`).
Release-channel tracking is a later extension of the same check; only
`refs/heads/main` is resolved today.

## How it works

- The OS scheduler runs `scripts/updater.py check` every 5 minutes
  (macOS: launchd agent `agent.mindie.kimi-update`; Windows: `schtasks`
  registration code exists but is **not natively verified**). There is
  no daemon, no model call, and no SessionStart work.
- One check resolves remote main to one SHA, stages that exact revision
  into a new immutable directory `update/generations/<sha>` with its own
  pinned venv built from `runtime-requirements.txt` (knowledge and
  remote-dev are pinned to full commit SHAs; `@main` is refused). Old
  scripts are never mixed with a new interpreter.
- The installed Kimi manifest points at absolute stable launchers
  (`update/launch/mindie_launch.py`) outside Kimi's managed plugin copy.
  The launcher resolves the current generation through the atomic
  `update/current.json` pointer and dispatches each call with that
  generation's own interpreter. Hooks hold the shared operation lock for
  their short run; the long-lived MCP front selects its generation under
  the shared lock and then execs it, so it never blocks updates.
  Already-loaded sessions keep their old generation callable because old
  generation directories are kept.
- The switch prepares the candidate fully, then under the exclusive
  operation lock calls the shared core authenticated RPC `stop_if_idle`.
  Only actual active capture/model/publication/feed/RPC work blocks;
  unknown/pending durable receipts and idle task grants do not. Busy or
  unavailable means: leave the current generation intact and defer to
  the next scheduled check. Remote task state lives under the stable
  per-task `state_dir/remote/<session>` path, so old job IDs remain
  pollable/stoppable after a switch.
- Install/update goes through Kimi's native plugin API
  (`scripts/install_kimi_plugin.py`, authenticated loopback
  `POST /api/v1/plugins` + GET readback of name/version). The native
  installer copies the built host package; a local-path install is not a
  live symlink. A small transaction receipt per revision is kept in
  `update/receipts/` and configs roll back if the swap fails. Old
  callable entrypoints and rollback generations are not deleted during a
  check; there is no retention quota — clean up explicitly only when safe.

## Commands

```
python scripts/updater.py check      # one bounded check (what the scheduler runs)
python scripts/updater.py status     # offline: current generation, last result/error
python scripts/updater.py recover    # explicit recovery: clear failed-revision suppression, re-check
python scripts/updater.py install-schedule / uninstall-schedule
```

`setup.py` registers the schedule by default (`--no-schedule` to skip)
and bootstraps `current.json` at the source tree.

## Failure behaviour

One bounded check, no write retry loop. `update/status.json` records
current/candidate SHA, last result and error, and `needs_host_reload`
after a successful switch. A failed exact revision is recorded in
`update/failed.json` and not reinstalled until `recover` or a newer
revision; read-only main discovery continues. An offline check failure
leaves the old version fully callable. Success is never inferred from a
ready HTTP port, pip's exit code, or copied files — the runtime probe,
native readback, and atomic pointer flip decide.

## Host reload boundary

After a switch, MCP and hook dispatch use the new generation for new
calls immediately. Native Skills, slash-command definitions, and hook
definitions in the manifest are loaded by the Kimi host; existing loaded
sessions do not pick those up until the host reloads/restarts. The
updater reports `needs_host_reload: true` rather than claiming live
sessions changed.

## Known gaps

- The pinned core in `runtime-requirements.txt` (`knowledge@6155846`)
  is an intermediate candidate; root supplies the final core commit. The
  installed acceptance core does not yet expose `stop_if_idle`, so a
  real switch currently defers honestly with
  "pinned core does not provide stop_if_idle".
- Windows task registration and lock parity are implemented but not
  natively verified; Windows runs scripts via `python` args, not
  shebangs.
