#!/usr/bin/env python3
"""Native OS schedule management for the MindIE Kimi updater (`mindie_harness.runtime.scheduler`).

[Role]
Writes the windowless Windows GUI wrapper (`render_windows_gui_wrapper` — zero
`CREATE_NEW_CONSOLE`), registers/migrates `MindIEKimiUpdate` and `agent.mindie.kimi-update.plist`
with verified readback, and unregisters either schedule cleanly.

[Invariants]
- Zero `CREATE_NEW_CONSOLE` or `SW_HIDE`: Windows wrappers use `mindie_harness.process.run_owned`
  (`CREATE_NO_WINDOW = 0x08000000`) launched by a PE-verified `Subsystem == 2` `pythonw.exe`.
- `refresh_existing_wrapper` updates `MindIEKimiUpdate.pyw` AND migrates any registered
  `MindIEKimiUpdate` Scheduled Task `<Command>` to the committed generation's `pythonw.exe`
  before old generations can be garbage-collected.
- Uninstall reports success only after native readback observes the schedule absent.

[Timeouts & Locks]
- Generated wrapper enforces a `1220s` deadline (`1210s` launcher + `10s` grace).
- Native scheduler registration/readback commands run under `10-30s` bounded deadlines.

[Side-Effect Policy]
- Writes `update/MindIEKimiUpdate.pyw`/`.xml`, user `LaunchAgents` plist, and OS scheduler
  registrations; `uninstall_schedule` removes only MindIE-owned entries.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

from mindie_harness.atomic import atomic_write_text
from mindie_harness.paths import HarnessError
from mindie_harness.process import verify_gui_subsystem
from mindie_harness.runtime.scheduler import render_windows_gui_wrapper

try:
    from bounded import run as bounded_run
    from genstate import launch_dir, read_current, update_dir
    from paths import config_path, load_adapter_config, which_host_binary
except ImportError:
    which_host_binary = None

INTERVAL_SECONDS = 300
WRAPPER_TIMEOUT_SECONDS = 1220.0
LAUNCHER = "mindie_launch.py"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

_HOST = None


def bind(host):
    """Bind to the re-exporting `updater` module for patchable lookups."""
    global _HOST
    _HOST = host


def _h(name):
    if _HOST is not None:
        try:
            return getattr(_HOST, name)
        except AttributeError:
            pass
    return globals()[name]


def _scheduler_backend():
    """Select only schedulers whose registration and readback commands exist."""
    resolve = (lambda name: which_host_binary(name, which_fn=shutil.which)) if which_host_binary else shutil.which
    if resolve("launchctl") and callable(getattr(os, "getuid", None)):
        return "launchd"
    if resolve("schtasks") and resolve("powershell"):
        return "task-scheduler"
    return None


def _wrapper_body(command) -> str:
    """Render a zero-popup Windows GUI `.pyw` wrapper via `mindie_harness.runtime.scheduler`."""
    return render_windows_gui_wrapper(list(command), timeout_seconds=WRAPPER_TIMEOUT_SECONDS)


def _schedule_wrapper(adapter: dict, command) -> Path:
    wrapper = _h("update_dir")(adapter) / "MindIEKimiUpdate.pyw"
    _h("update_dir")(adapter).mkdir(parents=True, exist_ok=True)
    atomic_write_text(wrapper, _h("_wrapper_body")(command), mode=0o600)
    return wrapper


def refresh_existing_wrapper(adapter, current):
    """Carry an already scheduled action (and Windows task `<Command>`) to the committed runtime."""
    wrapper = _h("update_dir")(adapter) / "MindIEKimiUpdate.pyw"
    if not wrapper.is_file():
        return False
    from paths import profile_config_path

    launcher = _h("launch_dir")(adapter) / (current.get("sha") or "bootstrap") / _h("LAUNCHER")
    if not launcher.is_file() or not (launcher.parent / "bounded.py").is_file():
        raise ValueError("committed scheduled launcher is unavailable")
    config_file = adapter.get("profile_config")
    if not (isinstance(config_file, str) and os.path.isabs(config_file)):
        config_file = str(profile_config_path())
    command = [current["python"], str(launcher), "--config", config_file, "updater", "check"]
    body = _h("_wrapper_body")(command)
    changed = wrapper.read_text(encoding="utf-8") != body
    if changed:
        _h("_schedule_wrapper")(adapter, command)
    if os.name == "nt" and _scheduler_backend() == "task-scheduler":
        pythonw = Path(current["python"]).with_name("pythonw.exe")
        try:
            _h("_require_windowless_python")(pythonw)
            state, _ = _h("_task_state")("MindIEKimiUpdate")
            if state == "present":
                _h("_register_windows_schedule")(adapter, command)
                changed = True
        except (SystemExit, RuntimeError, OSError):
            pass
    return changed


def _verify_scheduled_task(wrapper: Path, pythonw: Path) -> None:
    script = (
        "$ErrorActionPreference='Stop'; "
        "$xml = Export-ScheduledTask -TaskName MindIEKimiUpdate -ErrorAction Stop; "
        "$bytes = [Text.UTF8Encoding]::new($false).GetBytes([string]$xml); "
        "$out = [Console]::OpenStandardOutput(); "
        "$out.Write($bytes, 0, $bytes.Length); $out.Flush()"
    )
    output = _h("bounded_run")(
        ["powershell", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        "",
        timeout=30,
    )
    try:
        actions = ET.fromstring(output.lstrip("\ufeff")).findall("./{*}Actions/{*}Exec")
    except ET.ParseError:
        actions = []
    if (
        len(actions) != 1
        or actions[0].findtext("{*}Command") != str(pythonw)
        or actions[0].findtext("{*}Arguments") != subprocess.list2cmdline([str(wrapper)])
    ):
        raise SystemExit(
            "scheduled task readback does not match the windowless wrapper action; "
            "the registration is not trusted"
        )


def _require_windowless_python(python):
    """Verify PE `Subsystem == 2` (`IMAGE_SUBSYSTEM_WINDOWS_GUI`) via `mindie_harness.process`."""
    try:
        verify_gui_subsystem(Path(python))
    except TimeoutError:
        raise
    except (HarnessError, OSError, ValueError):
        raise SystemExit(
            f"a PE GUI windowless Python is required at {python}; task was not registered"
        ) from None


def _register_windows_schedule(adapter: dict, command) -> dict:
    pythonw = Path(command[0]).with_name("pythonw.exe")
    _h("_require_windowless_python")(pythonw)
    wrapper = _h("_schedule_wrapper")(adapter, command)
    task = ET.Element("Task", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task", version="1.2")
    trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "TimeTrigger")
    start = datetime.now() + timedelta(seconds=_h("INTERVAL_SECONDS"))
    ET.SubElement(trigger, "StartBoundary").text = start.isoformat(timespec="seconds")
    ET.SubElement(ET.SubElement(trigger, "Repetition"), "Interval").text = "PT5M"
    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="Author")
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    settings = ET.SubElement(task, "Settings")
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    ET.SubElement(settings, "Hidden").text = "true"
    action = ET.SubElement(ET.SubElement(task, "Actions", Context="Author"), "Exec")
    ET.SubElement(action, "Command").text = str(pythonw)
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline([str(wrapper)])
    definition = wrapper.with_suffix(".xml")
    ET.ElementTree(task).write(definition, encoding="utf-16", xml_declaration=True)
    _h("bounded_run")(
        ["schtasks", "/Create", "/F", "/TN", "MindIEKimiUpdate", "/XML", str(definition)],
        "",
        timeout=30,
    )
    _h("_verify_scheduled_task")(wrapper, pythonw)
    return {"result": "scheduled", "task": "MindIEKimiUpdate", "action": str(wrapper), "verified": True}


def install_schedule(config_file=None) -> int:
    if config_file is not None:
        config_file = Path(config_file).expanduser().absolute()
        adapter = json.loads(config_file.read_text(encoding="utf-8"))
        if not isinstance(adapter, dict):
            raise SystemExit("adapter configuration must be one JSON object")
    else:
        adapter = _h("load_adapter_config")()
        config_file = _h("config_path")()
    current = _h("read_current")(adapter)
    launcher = _h("launch_dir")(adapter) / (current.get("sha") or "bootstrap") / _h("LAUNCHER")
    if not launcher.is_file() or not (launcher.parent / "bounded.py").is_file():
        raise SystemExit(f"no launcher available at {launcher.parent}")
    command = _h("schedule_command")(launcher, config_file)
    from mindie_diagnostics.service_environment import prepare

    prepare(_h("update_dir")(adapter))
    backend = _scheduler_backend()
    if backend == "launchd":
        import plistlib

        plist = Path.home() / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        with plist.open("wb") as stream:
            plistlib.dump(
                {
                    "Label": "agent.mindie.kimi-update",
                    "ProgramArguments": command,
                    "EnvironmentVariables": {
                        "PATH": os.environ.get("PATH")
                        or "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
                    },
                    "KeepAlive": False,
                    "StartInterval": _h("INTERVAL_SECONDS"),
                    "StandardOutPath": os.devnull,
                    "StandardErrorPath": os.devnull,
                },
                stream,
            )
        uid = os.getuid()
        try:
            _h("bounded_run")(["launchctl", "bootout", f"gui/{uid}", str(plist)], "", timeout=15)
        except RuntimeError:
            pass
        _h("bounded_run")(["launchctl", "bootstrap", f"gui/{uid}", str(plist)], "", timeout=15)
        print(json.dumps({"result": "scheduled", "agent": str(plist), "interval_seconds": _h("INTERVAL_SECONDS")}))
        return 0
    if backend == "task-scheduler":
        print(json.dumps(_h("_register_windows_schedule")(adapter, command)))
        return 0
    print(
        json.dumps(
            {
                "result": "unsupported-platform",
                "hint": f"run every {_h('INTERVAL_SECONDS')}s via your scheduler: " + " ".join(command),
            }
        )
    )
    return 1


def _native_run(argv, timeout):
    completed = subprocess.run(
        argv,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _launchd_state(label, uid, timeout=10):
    try:
        code, _, stderr = _h("_native_run")(["launchctl", "print", f"gui/{uid}/{label}"], timeout)
    except TimeoutError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"[:240]
    if code == 0:
        return "loaded", ""
    if code == 113:
        return "absent", ""
    return "unknown", (stderr or "").strip()[:240] or f"launchctl print exited {code}"


def _task_state(task, timeout=30):
    escaped = task.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$m = @(Get-ScheduledTask -ErrorAction Stop | Where-Object { "
        f"$_.TaskName -eq '{escaped}' -and $_.TaskPath -eq '\\' }}); "
        "Write-Output $m.Count"
    )
    try:
        code, stdout, stderr = _h("_native_run")(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout
        )
    except TimeoutError:
        raise
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "unknown", f"{type(exc).__name__}: {exc}"[:240]
    if code != 0:
        return "unknown", (stderr or "").strip()[:240] or f"powershell exited {code}"
    try:
        count = int((stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return "unknown", "unparseable task enumeration"
    return ("present" if count else "absent"), ""


def _schedule_failed(stage, category, *, plist=None, task=None, detail=""):
    payload = {"result": "failed", "stage": stage, "category": category}
    if plist is not None:
        payload["retained_plist"] = str(plist)
    if task is not None:
        payload["retained_task"] = task
    if detail:
        payload["error"] = str(detail)[:240]
    print(json.dumps(payload))
    return 1


def _uninstall_launchd(label, plist):
    uid = os.getuid()
    target = f"gui/{uid}/{label}"
    state, detail = _h("_launchd_state")(label, uid)
    if state == "unknown":
        return _h("_schedule_failed")("query", "manager-or-permission", plist=plist, detail=detail)
    if state == "loaded":
        bootout_detail = ""
        try:
            code, _, stderr = _h("_native_run")(["launchctl", "bootout", target], 15)
            if code:
                bootout_detail = (stderr or "").strip()[:240]
        except TimeoutError:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            bootout_detail = f"{type(exc).__name__}: {exc}"[:240]
        cutoff = time.monotonic() + 3.0
        while state != "absent" and time.monotonic() < cutoff:
            time.sleep(0.2)
            left = cutoff - time.monotonic()
            if left <= 0:
                break
            state, detail = _h("_launchd_state")(label, uid, timeout=min(5.0, left))
        if state != "absent":
            return _h("_schedule_failed")(
                "readback",
                "service-retained" if state == "loaded" else "state-unproven",
                plist=plist,
                detail=detail or bootout_detail,
            )
    plist.unlink(missing_ok=True)
    print(json.dumps({"result": "unscheduled"}))
    return 0


def _uninstall_task(task):
    state, detail = _h("_task_state")(task)
    if state == "unknown":
        return _h("_schedule_failed")("query", "manager-or-permission", task=task, detail=detail)
    if state == "present":
        escaped = task.replace("'", "''")
        script = f"Unregister-ScheduledTask -TaskName '{escaped}' -TaskPath '\\' -Confirm:$false -ErrorAction Stop"
        try:
            code, _, stderr = _h("_native_run")(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script], 30
            )
            if code:
                detail = (stderr or "").strip()[:240]
        except TimeoutError:
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            detail = f"{type(exc).__name__}: {exc}"[:240]
        cutoff = time.monotonic() + 3.0
        while state != "absent" and time.monotonic() < cutoff:
            time.sleep(0.2)
            left = cutoff - time.monotonic()
            if left <= 0:
                break
            state, query_detail = _h("_task_state")(task, timeout=min(10.0, left))
            if state != "present":
                detail = query_detail or detail
        if state != "absent":
            return _h("_schedule_failed")(
                "readback",
                "service-retained" if state == "present" else "state-unproven",
                task=task,
                detail=detail,
            )
    print(json.dumps({"result": "unscheduled"}))
    return 0


def uninstall_schedule(*, label=None, plist_path=None, schedule_root=None, task=None) -> int:
    backend = _scheduler_backend()
    if backend == "launchd":
        label = label or "agent.mindie.kimi-update"
        if plist_path is not None:
            plist = Path(plist_path).expanduser().absolute()
        else:
            root = Path(schedule_root).expanduser().absolute() if schedule_root else Path.home() / "Library" / "LaunchAgents"
            plist = root / (label + ".plist")
        return _h("_uninstall_launchd")(label, plist)
    if backend == "task-scheduler":
        return _h("_uninstall_task")(task or "MindIEKimiUpdate")
    print(
        json.dumps(
            {
                "result": "unsupported-platform",
                "hint": "remove the agent.mindie.kimi-update schedule with your platform tools",
            }
        )
    )
    return 1
