# MindIE Kimi automatic update

Tracks `main` of the plugin remote (default
`https://github.com/mindie-agent/mindie-agent-kimi.git`, override with
`update_remote` in the adapter config or `MINDIE_KIMI_UPDATE_REMOTE`).
Release-channel tracking is a later extension of the same check; only
`refs/heads/main` is resolved today.

## How it works

- The OS scheduler runs the stable front in updater mode every 5 minutes
  (macOS: launchd agent `agent.mindie.kimi-update`, written via plistlib;
  Windows: `schtasks` registration code exists but is **not natively
  verified**). No daemon, no model call, no SessionStart work.
- One check is serialized by a nonblocking `check.lock` and bounded by
  one absolute deadline (240s). It resolves remote main to one SHA and
  stages that exact revision into `update/generations/<sha>`: immutable
  code, its own pinned venv built at the final path from
  `runtime-requirements.txt` (full commit SHAs only; `@main` refused;
  pip runs with `PIP_RETRIES=0`, no prompts), plus its own
  adapter/engine JSON under `config/`. Admission, community, state_dir
  and first-use paths stay stable; parser/organizer/interpreter move
  with the generation.
- `update/current.json` is ONE atomic committed tuple
  `{generation, python, adapter_config, sha}`. The launcher sets
  `MINDIE_KIMI_CONFIG` to the tuple's adapter config per child, so code,
  interpreter and config always come from the same committed generation.
- The host package manifest points at a NEW versioned launcher path per
  revision (`update/launch/<sha>/mindie_launch.py`); staging never
  mutates live entrypoints, and prior launcher paths stay callable. The
  bootstrap launcher lives at `update/launch/bootstrap/`.
- The switch runs under the exclusive operation lock: core
  `stop_if_idle` is called through the CURRENT committed interpreter
  (fails closed on import/RPC/API problems; a truly absent service
  endpoint is idle; unknown/pending receipts and idle grants never
  block). Then the native plugin API install with exact identifier, version, enabled state and source-path
  readback; uncertain outcomes are reconciled against the native
  registry before any verdict. Only then does `current.json` flip.
  Rollback restores the previous pointer AND the previous native
  package/readback, recorded in status, never claimed as success.
- Feed sync is independent: `mindie_knowledge.loop.cli sync --config
  <engine>` runs with the current committed interpreter under the shared
  operation lock even when the plugin candidate fails or is unchanged.
  No organizer, no model, no capture.

## Commands

```
python scripts/updater.py check      # one bounded check (what the scheduler runs)
python scripts/updater.py status     # offline: current tuple, last result/error
python scripts/updater.py recover    # quarantined or unknown SHA only; network retries without this
python scripts/updater.py install-schedule / uninstall-schedule
```

`setup.py` builds its own pinned venv by default (`--knowledge-python`
is an operator override; Python 3.11+ required), persists the explicit
Kimi home (default `$KIMI_CODE_HOME` or `~/.kimi-code`), writes the
bootstrap launcher and `current.json` tuple, and registers the schedule
unless `--no-schedule`.

## Failure behaviour

One bounded check, and no retry loop inside that check. The existing
scheduler is the only timer. `update/status.json` records current/candidate
SHA, last result and error, `feed_sync`, `rollback`, and
`needs_host_reload`. A known temporary network or rate-limit failure keeps
the previous install, records `next_retry_at`, and is tried again on a later
scheduled check of the same SHA. Bad content stays quarantined. Certificate
verification is a local trust failure, not invalid SHA content; the same
schedule retries it after backoff, and TLS validation stays enabled. An unknown
or crashed install still rolls back before another mutation and is not
retried just because a command timed out; `recover` clears that suppression.
Success clears the active retry and error. Attempt count and the first
failure time can remain. An offline check leaves the old version callable.
Success is never inferred from a ready HTTP port, pip's exit code, or copied
files.

## Host reload boundary

After a switch, MCP and hook dispatch use the new generation per call.
Already-loaded entrypoint paths stay callable and select the committed
generation for each new operation. Native Skills, slash-command, MCP tool
definitions and hook definitions refresh according to the Kimi host lifecycle; the updater reports
`needs_host_reload: true` rather than claiming live sessions changed.

## Known gaps

- Windows task registration and lock parity are implemented but not
  natively verified; Windows runs scripts via `python` args, not
  shebangs.
- Release-channel tracking is not yet implemented (main only).
