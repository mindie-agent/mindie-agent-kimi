#!/usr/bin/env python3
"""Rebind already-installed host entries and schedulers to the committed runtime.

[Role]
After a plugin switch or a component-only runtime switch, atomically carry the
managed Windows wrapper, the macOS LaunchAgent plist, the Kimi managed plugin
entry (`mindie-front` + `kimi.plugin.json`) and an existing native record's
`originalSource` to the newly committed python/generation/launcher.

[Invariants]
- Only files that already exist and are recognizably MindIE-owned are touched;
  a foreign plist, wrapper or plugin directory is left byte-for-byte intact.
- No scheduler is registered, reloaded, enabled or disabled here. Every
  replaced scheduler runtime is persisted in a bounded `scheduled-pins.json`
  before the host file changes: the first replaced `(python, launcher)` pair is
  the permanent `anchor`, plus at most `MAX_REPLACED_RECORDS` recent pairs. The
  anchor keeps a launchd job that still holds the original command alive, while
  the slot ring covers every intermediate reload.
- A cleanup failure after a committed switch is reported as a receipt warning;
  it never rolls back the committed generation.

[Timeouts & Locks]
- One short monotonic budget bounds every step. The caller may already hold the
  exclusive operation lock; this module takes no additional lock.

[Side-Effect Policy]
- Rewrites only the managed wrapper/plist, Kimi's installed `mindie-front`,
  `kimi.plugin.json`, `scheduled-pins.json`, and the matching `originalSource`
  in `installed.json`; every replacement uses a temporary file + `os.replace`.
"""

from __future__ import annotations

import json
import os
import plistlib
import shlex
import stat
import time
from pathlib import Path

SCHEMA = "mindie-kimi-host-bindings/1"
REFRESH_BUDGET_SECONDS = 10.0
LABEL = "agent.mindie.kimi-update"
LAUNCHER = "mindie_launch.py"
PLUGIN_IDS = frozenset({"mindie-agent", "mindie"})
SCHEDULED_PINS = "scheduled-pins.json"
# The anchor covers the command launchd loaded at boot; four recent pairs cover
# any reload between upgrades. This keeps the JSON file far below 4 KiB.
MAX_REPLACED_RECORDS = 4
SCHEDULED_PINS_SCHEMA = "mindie-kimi-scheduled-pins/2"
LEGACY_SCHEDULED_PINS_SCHEMA = "mindie-kimi-scheduled-pins/1"
PIN_KEYS = ("python", "launcher")


def _same_path(first, second):
    from mindie_knowledge.filesystem import path_identity

    try:
        return path_identity(Path(first)) == path_identity(Path(second))
    except TimeoutError:
        raise
    except (OSError, RuntimeError, ValueError):
        return (
            os.path.normcase(os.path.abspath(str(first)))
            == os.path.normcase(os.path.abspath(str(second)))
        )


def _atomic_bytes(path, payload, mode=None):
    path = Path(path)
    temporary = path.with_name(path.name + f".tmp-{os.getpid()}")
    try:
        temporary.write_bytes(payload)
        if mode is not None:
            try:
                os.chmod(temporary, mode)
            except TimeoutError:
                raise
            except OSError:
                pass
        os.replace(temporary, path)
    finally:
        try:
            if temporary.exists():
                temporary.unlink()
        except TimeoutError:
            raise
        except OSError:
            pass


def _mode(path):
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except TimeoutError:
        raise
    except OSError:
        return None


def _read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8-sig")
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None


def _read_json(path):
    text = _read_text(path)
    if text is None:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def scheduled_pins_path(adapter):
    from genstate import update_dir

    return update_dir(adapter) / SCHEDULED_PINS


def _pin_key(value):
    text = str(value)
    if os.name == "nt" and text.startswith("\\\\?\\"):
        # A `\\?\`-prefixed identity is the same file as its plain twin.
        text = text[4:]
        if text[:4].upper() == "UNC\\":
            text = "\\\\" + text[4:]
    return os.path.normcase(os.path.abspath(text))


def _pin_slot(python, launcher, recorded_at):
    return {"python": python, "launcher": launcher, "recorded_at": recorded_at}


def _pin_value(value, now=None):
    """Normalize one persisted slot, dropping non-absolute/foreign fields."""
    if not isinstance(value, dict):
        return None
    python = value.get("python")
    launcher = value.get("launcher")
    python = python if isinstance(python, str) and os.path.isabs(python) else None
    launcher = launcher if isinstance(launcher, str) and os.path.isabs(launcher) else None
    if python is None and launcher is None:
        return None
    recorded_at = value.get("recorded_at")
    if not isinstance(recorded_at, (int, float)) or isinstance(recorded_at, bool):
        recorded_at = now
    return _pin_slot(python, launcher, recorded_at)


def _pin_records(paths, now):
    """Pair replaced scheduler paths into bounded slot records."""
    pythons, launchers = [], []
    for item in paths:
        if not isinstance(item, str) or not os.path.isabs(item):
            continue
        bucket = (launchers if os.path.basename(item).casefold() == LAUNCHER.casefold()
                  else pythons)
        if all(_pin_key(existing) != _pin_key(item) for existing in bucket):
            bucket.append(item)
    return [
        _pin_slot(
            pythons[index] if index < len(pythons) else None,
            launchers[index] if index < len(launchers) else None,
            now,
        )
        for index in range(max(len(pythons), len(launchers)))
    ]


def _legacy_state(pins):
    """Migrate the 0.1.26 flat ring: its first path pair becomes the anchor."""
    records = _pin_records(pins if isinstance(pins, list) else [], None)
    anchor = records.pop(0) if records else None
    return anchor, records[:MAX_REPLACED_RECORDS]


def _pin_state(adapter):
    value = _read_json(scheduled_pins_path(adapter))
    if not isinstance(value, dict):
        return None, []
    if value.get("schema") == SCHEDULED_PINS_SCHEMA:
        anchor = _pin_value(value.get("anchor"))
        records = []
        raw = value.get("records")
        for item in raw if isinstance(raw, list) else []:
            if (slot := _pin_value(item)) is not None:
                records.append(slot)
        return anchor, records
    if value.get("schema") == LEGACY_SCHEDULED_PINS_SCHEMA or "pins" in value:
        return _legacy_state(value.get("pins"))
    return None, []


def _flatten_pins(anchor, records):
    values = []
    for slot in ([anchor] if anchor is not None else []) + list(records):
        for key in PIN_KEYS:
            item = slot.get(key)
            if isinstance(item, str) and os.path.isabs(item) and item not in values:
                values.append(item)
    return values


def _pin_exists(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        return Path(value).exists()
    except TimeoutError:
        raise
    except OSError:
        return False


def _merge_record(records, candidate):
    for slot in records:
        if (slot.get("python") and candidate.get("python")
                and _pin_key(slot["python"]) == _pin_key(candidate["python"])):
            if slot.get("launcher") is None and candidate.get("launcher") is not None:
                slot["launcher"] = candidate["launcher"]
            slot["recorded_at"] = candidate["recorded_at"]
            return
    records.append(candidate)


def scheduled_pins(adapter):
    """Return every protected scheduler runtime path; the anchor comes first."""
    anchor, records = _pin_state(adapter)
    return _flatten_pins(anchor, records)


def remember_scheduled_pins(adapter, paths):
    """Persist replaced scheduler runtimes before their host file is rewritten.

    A still-loaded launchd job keeps dispatching the command it read at load
    time even though the on-disk plist now names the committed runtime. The
    first replaced pair is therefore an `anchor` that survives every later
    upgrade (until its files disappear); the most recent
    `MAX_REPLACED_RECORDS` pairs cover intermediate reloads. Both bounds are
    tiny, so rapid upgrades can never evict the runtime the scheduler holds.
    """
    now = time.time()
    fresh = _pin_records(paths, now)
    if not fresh:
        return scheduled_pins(adapter)
    previous_anchor, previous_records = _pin_state(adapter)
    anchor = previous_anchor
    if anchor is None:
        anchor = dict(fresh[0])
    records = [dict(slot) for slot in previous_records]
    for candidate in fresh:
        if (anchor.get("python") and candidate.get("python")
                and _pin_key(anchor["python"]) == _pin_key(candidate["python"])):
            if anchor.get("launcher") is None and candidate.get("launcher") is not None:
                anchor["launcher"] = candidate["launcher"]
            continue
        _merge_record(records, candidate)
    records = records[-MAX_REPLACED_RECORDS:]
    if anchor is not None and not (
            _pin_exists(anchor.get("python")) or _pin_exists(anchor.get("launcher"))):
        # Only a vanished anchor is released. Running from a newer interpreter
        # (including a manual run) never proves launchd reloaded its command.
        anchor = None
    flat = _flatten_pins(anchor, records)
    if anchor == previous_anchor and records == previous_records:
        return flat
    from genstate import atomic_write

    atomic_write(scheduled_pins_path(adapter), {
        "schema": SCHEDULED_PINS_SCHEMA,
        "anchor": anchor,
        "records": records,
        "updated_at": now,
    })
    return flat


def _scheduler_paths(arguments, previous=None):
    """Paths a still-loaded scheduler command may keep in use after a rewrite."""
    candidates = []
    if isinstance(arguments, (list, tuple)):
        if arguments:
            first = arguments[0]
            if isinstance(first, str) and os.path.isabs(first):
                candidates.append(first)
        candidates.extend(
            item for item in arguments
            if isinstance(item, str) and os.path.isabs(item)
            and item.endswith(LAUNCHER))
    if isinstance(previous, dict) and isinstance(previous.get("python"), str):
        candidates.append(previous["python"])
    return candidates


def _python(current):
    value = current.get("python") if isinstance(current, dict) else None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    try:
        return path if path.is_file() else None
    except TimeoutError:
        raise
    except OSError:
        return None


def _launcher(adapter, current):
    from genstate import launch_dir

    sha = current.get("sha") if isinstance(current, dict) else None
    path = launch_dir(adapter) / (sha if isinstance(sha, str) and sha else "bootstrap") / LAUNCHER
    try:
        return path if path.is_file() else None
    except TimeoutError:
        raise
    except OSError:
        return None


def _owns_launch_arguments(adapter, arguments):
    from genstate import launch_dir

    if not isinstance(arguments, (list, tuple)):
        return False
    for item in arguments:
        if not isinstance(item, str) or not item.endswith(LAUNCHER):
            continue
        try:
            if _same_path(Path(item).parent.parent, launch_dir(adapter)):
                return True
        except TimeoutError:
            raise
        except (OSError, ValueError):
            continue
    return False


def _wrapper_command(adapter, current):
    python = _python(current)
    launcher = _launcher(adapter, current)
    if python is None or launcher is None:
        return None
    config_file = adapter.get("profile_config") if isinstance(adapter, dict) else None
    if not (isinstance(config_file, str) and os.path.isabs(config_file)):
        from paths import profile_config_path

        config_file = str(profile_config_path())
    return [str(python), str(launcher), "--config", str(config_file),
            "updater", "check"]


def _host_command(adapter, current, *, config=None):
    python = _python(current)
    launcher = _launcher(adapter, current)
    if python is None or launcher is None:
        return None
    return [str(python), str(launcher), "--config", str(config or current.get("adapter_config"))]


def _refresh_wrapper(adapter, current, previous=None):
    from genstate import launch_dir, update_dir

    wrapper = update_dir(adapter) / "MindIEKimiUpdate.pyw"
    try:
        if not wrapper.is_file():
            return None
    except TimeoutError:
        raise
    except OSError:
        return None
    from updater_schedule import refresh_existing_wrapper

    old = previous.get("python") if isinstance(previous, dict) else None
    if isinstance(old, str) and os.path.isabs(old):
        replaced = [old]
        sha = previous.get("sha")
        if isinstance(sha, str) and sha:
            replaced.append(str(launch_dir(adapter) / sha / LAUNCHER))
        remember_scheduled_pins(adapter, replaced)
    return ["schedule-wrapper"] if refresh_existing_wrapper(adapter, current) else None


def _refresh_launchd(adapter, current, previous=None):
    path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    try:
        if not path.is_file():
            return None
        value = plistlib.loads(path.read_bytes())
    except TimeoutError:
        raise
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    arguments = value.get("ProgramArguments")
    if not _owns_launch_arguments(adapter, arguments):
        return None
    command = _wrapper_command(adapter, current)
    if command is None or list(arguments) == command:
        return None
    # Persist before rewriting: if the pin record cannot be written the old
    # host file (and therefore the still-loaded job) stays untouched.
    remember_scheduled_pins(adapter, _scheduler_paths(arguments, previous))
    value["ProgramArguments"] = command
    value["Label"] = LABEL
    payload = plistlib.dumps(value, fmt=plistlib.FMT_XML, sort_keys=True)
    _atomic_bytes(path, payload, _mode(path))
    return ["launchd-plist"]


def _refresh_front(path, current):
    text = _read_text(path)
    if text is None:
        return None
    first = text.splitlines()[0] if text.splitlines() else ""
    if not first.startswith("#!") or "exec " not in text:
        return None
    python = _python(current)
    if python is None:
        return None
    body = "#!/bin/sh\nexec " + shlex.quote(str(python)) + ' "$@"\n'
    if text == body:
        return None
    _atomic_bytes(path, body.encode("utf-8"), _mode(path) or 0o755)
    return "managed-front"


def _plugin_manifest(path):
    manifest = _read_json(path)
    if manifest is None:
        return None
    name = manifest.get("name")
    if not (isinstance(name, str) and name in PLUGIN_IDS):
        servers = manifest.get("mcpServers")
        if not isinstance(servers, dict) or "remote" not in servers:
            return None
    return manifest


def _refresh_manifest(path, manifest, adapter, current, previous):
    command = _host_command(adapter, current)
    if command is None:
        return None
    launcher = command[1]
    config = command[3]
    changed = False
    old_launchers = set()
    servers = manifest.get("mcpServers")
    if isinstance(servers, dict):
        for entry in servers.values():
            if not isinstance(entry, dict):
                continue
            arguments = entry.get("args")
            if not isinstance(arguments, list):
                continue
            for index, item in enumerate(arguments):
                if isinstance(item, str) and item.endswith(LAUNCHER) and item != launcher:
                    old_launchers.add(item)
                    arguments[index] = launcher
                    changed = True
            if "--config" in arguments:
                index = arguments.index("--config")
                if index + 1 < len(arguments) and arguments[index + 1] != config:
                    arguments[index + 1] = config
                    changed = True
            if os.name == "nt":
                # The Windows package launches the bare `python.exe` and pins
                # its directory in PATH; carrying that PATH is the front-wrapper
                # rebind equivalent on Windows.
                environment = entry.get("env")
                path_value = environment.get("PATH") if isinstance(environment, dict) else None
                if isinstance(path_value, str):
                    parts = path_value.split(os.pathsep)
                    parts[0] = str(Path(command[0]).parent)
                    updated = os.pathsep.join(parts)
                    if updated != path_value:
                        environment["PATH"] = updated
                        changed = True
    replacements = [(item, launcher) for item in old_launchers]
    if isinstance(previous, dict):
        from genstate import launch_dir

        if isinstance(previous.get("sha"), str) and previous["sha"]:
            replacements.append((
                str(launch_dir(adapter) / previous["sha"] / LAUNCHER), launcher))
        if isinstance(previous.get("python"), str):
            replacements.append((previous["python"], command[0]))
        if isinstance(previous.get("adapter_config"), str):
            replacements.append((previous["adapter_config"], config))
    hooks = manifest.get("hooks")
    if isinstance(hooks, list):
        for hook in hooks:
            if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                continue
            command_text = hook["command"]
            for old, new in replacements:
                if old and old != new and old in command_text:
                    command_text = command_text.replace(old, new)
            if command_text != hook["command"]:
                hook["command"] = command_text
                changed = True
    if not changed:
        return None
    _atomic_bytes(path, (json.dumps(manifest, indent=2) + "\n").encode("utf-8"), _mode(path))
    return "managed-manifest"


def _old_generation_source(value):
    from mindie_knowledge.storage import owning_dir

    if not isinstance(value, str) or not value:
        return False
    try:
        return owning_dir(value) is not None
    except TimeoutError:
        raise
    except Exception:
        return False


def _refresh_installed_record(kimi_home, current):
    path = Path(kimi_home) / "plugins" / "installed.json"
    value = _read_json(path)
    if value is None:
        return None
    plugins = value.get("plugins")
    if not isinstance(plugins, list):
        return None
    generation = current.get("generation") if isinstance(current, dict) else None
    if not isinstance(generation, str) or not generation:
        return None
    target = Path(generation) / "host-package"
    try:
        if not target.is_dir():
            return None
    except TimeoutError:
        raise
    except OSError:
        return None
    changed = False
    for record in plugins:
        if not isinstance(record, dict) or record.get("id") not in PLUGIN_IDS:
            continue
        source = record.get("originalSource")
        if not _old_generation_source(source):
            continue
        if _same_path(source, target):
            continue
        record["originalSource"] = str(target)
        changed = True
    if not changed:
        return None
    _atomic_bytes(path, (json.dumps(value, indent=2) + "\n").encode("utf-8"), _mode(path))
    return "native-record"


def _refresh_managed(adapter, current, previous):
    kimi_home = adapter.get("kimi_home") if isinstance(adapter, dict) else None
    if not isinstance(kimi_home, str) or not kimi_home:
        kimi_home = os.environ.get("KIMI_CODE_HOME")
    if not isinstance(kimi_home, str) or not kimi_home:
        return None
    root = Path(kimi_home) / "plugins" / "managed" / "mindie-agent"
    refreshed = []
    try:
        managed = root.is_dir()
    except TimeoutError:
        raise
    except OSError:
        managed = False
    if managed:
        front = _refresh_front(root / "mindie-front", current)
        if front:
            refreshed.append(front)
        manifest_path = root / "kimi.plugin.json"
        manifest = _plugin_manifest(manifest_path)
        if manifest is not None:
            changed = _refresh_manifest(manifest_path, manifest, adapter, current, previous)
            if changed:
                refreshed.append(changed)
    record = _refresh_installed_record(kimi_home, current)
    if record:
        refreshed.append(record)
    return refreshed or None


def refresh_after_switch(adapter, previous, current, deadline=None):
    """Best-effort bounded rebind after a committed switch.

    Timeout and ordinary cleanup failures become `warnings` because the
    generation pointer is already committed. They are surfaced as a
    `receipt_warning`; they must never turn the successful switch into a fault.
    """
    limit = time.monotonic() + REFRESH_BUDGET_SECONDS
    if deadline is not None:
        limit = min(limit, deadline)
    result = {"schema": SCHEMA, "status": "ok", "refreshed": [], "warnings": []}
    steps = (
        ("schedule-wrapper", lambda: _refresh_wrapper(adapter, current, previous)),
        ("launchd-plist", lambda: _refresh_launchd(adapter, current, previous)),
        ("managed-entry", lambda: _refresh_managed(adapter, current, previous)),
    )
    for name, step in steps:
        if time.monotonic() >= limit:
            result["warnings"].append(name + ": budget exhausted")
            continue
        try:
            refreshed = step()
        except TimeoutError:
            result["warnings"].append(name + ": timeout")
            continue
        except Exception as exc:
            result["warnings"].append(name + ": " + type(exc).__name__)
            continue
        if refreshed:
            result["refreshed"].extend(refreshed)
    if result["warnings"]:
        result["status"] = "partial"
    return result


def refresh_and_record(adapter, previous, current, deadline=None):
    """Refresh bindings and attach a receipt warning when cleanup degraded.

    The generation pointer is already committed here. A timeout or receipt
    write failure is therefore recorded as data, not re-raised as a switch
    fault; the next scheduled check retries the same idempotent refresh.
    """
    try:
        result = refresh_after_switch(adapter, previous, current, deadline)
    except TimeoutError:
        result = {"schema": SCHEMA, "status": "partial", "refreshed": [],
                  "warnings": ["timeout"]}
    except Exception as exc:
        result = {"schema": SCHEMA, "status": "partial", "refreshed": [],
                  "warnings": [type(exc).__name__]}
    if not result.get("warnings"):
        return result
    try:
        from genstate import atomic_write, read_json, receipts_dir

        sha = current.get("sha") if isinstance(current, dict) else None
        if isinstance(sha, str) and sha:
            path = receipts_dir(adapter) / f"{sha}.json"
            receipt = read_json(path) or {}
            if isinstance(receipt, dict):
                receipt["receipt_warning"] = result
                atomic_write(path, receipt)
    except TimeoutError:
        result["warnings"].append("receipt: timeout")
    except Exception as exc:
        result["warnings"].append("receipt: " + type(exc).__name__)
    return result
