#!/usr/bin/env python3
"""Native OS schedule management for the MindIE Kimi updater.

[Role]
Writes the windowless Windows wrapper and registered task, writes and
bootstraps the launchd plist, queries native scheduler state, and unregisters
either schedule with explicit readback. `updater.py` re-exports these helpers
and binds itself so monkeypatches keep resolving through it.

[Invariants]
- Registration is only reported as scheduled after native readback proves the
  exact wrapper action.
- Uninstall reports success only after the scheduler state is observed absent.
- The generated wrapper covers the 1210s launcher budget plus 10s cleanup.
- Scheduler dispatch requires the actual native commands, independent of OS labels.

[Timeouts & Locks]
- `subprocess.run(..., timeout=1220)` in the generated wrapper; `_native_run`
  callers pass explicit 10-30s budgets and uninstall readback polls for 3s.

[Side-Effect Policy]
- Writes `update/MindIEKimiUpdate.pyw`/`.xml`, the user LaunchAgents plist, and
  launchd/Task Scheduler registrations; uninstall removes only its own entries.
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

try:
    from bounded import run as bounded_run
    from genstate import read_current, update_dir
    from paths import config_path, load_adapter_config
except ImportError:
    pass

INTERVAL_SECONDS = 300
LAUNCHER = "mindie_launch.py"

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
    if shutil.which("launchctl") and callable(getattr(os, "getuid", None)):
        return "launchd"
    if shutil.which("schtasks") and shutil.which("powershell"):
        return "task-scheduler"
    return None


def _wrapper_body(command) -> str:
    # Keep a hidden console for the launcher and its ordinary console children.
    # CREATE_NO_WINDOW alone would let those children allocate a visible console.
    return (
        "import subprocess\n"
        "startup = subprocess.STARTUPINFO()\n"
        "startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW\n"
        "startup.wShowWindow = subprocess.SW_HIDE\n"
        f"raise SystemExit(subprocess.run({list(command)!r},\n"
        "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,\n"
        "    startupinfo=startup, creationflags=subprocess.CREATE_NEW_CONSOLE,\n"
        "    timeout=1220, check=False).returncode)\n"
    )


def _schedule_wrapper(adapter: dict, command) -> Path:
    wrapper = _h("update_dir")(adapter) / "MindIEKimiUpdate.pyw"
    _h("update_dir")(adapter).mkdir(parents=True, exist_ok=True)
    body = _h("_wrapper_body")(command)
    tmp = wrapper.with_name(wrapper.name + f".tmp-{os.getpid()}")
    tmp.write_text(body, encoding="utf-8")
    os.replace(tmp, wrapper)
    return wrapper



def refresh_existing_wrapper(adapter, current):
    """Carry an already scheduled action to the committed launcher and budget.

    Replacing the managed script preserves the native task's enabled/disabled
    choice and never re-registers a task or terminates its current invocation.
    """
    wrapper = _h("update_dir")(adapter) / "MindIEKimiUpdate.pyw"
    if not wrapper.is_file():
        return False
    from paths import profile_config_path
    launcher = _h("launch_dir")(adapter) / (current.get("sha") or "bootstrap") / _h("LAUNCHER")
    if not launcher.is_file() or not (launcher.parent / "bounded.py").is_file():
        raise ValueError("committed scheduled launcher is unavailable")
    command = [current["python"], str(launcher), "--config", str(profile_config_path()),
               "updater", "check"]
    if wrapper.read_text(encoding="utf-8") == _h("_wrapper_body")(command):
        return False
    _h("_schedule_wrapper")(adapter, command)
    return True


def _verify_scheduled_task(wrapper: Path, pythonw: Path) -> None:
    # schtasks writes its XML in the console codepage. Export Unicode directly
    # so bounded_run's UTF-8 transport also preserves non-ASCII paths.
    output = _h("bounded_run")([
        "powershell", "-NoProfile", "-NonInteractive", "-Command",
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new(); "
        "Export-ScheduledTask -TaskName MindIEKimiUpdate -ErrorAction Stop",
    ], "", timeout=30)
    try:
        actions = ET.fromstring(output).findall("./{*}Actions/{*}Exec")
    except ET.ParseError:
        actions = []
    if (len(actions) != 1
            or actions[0].findtext("{*}Command") != str(pythonw)
            or actions[0].findtext("{*}Arguments") != subprocess.list2cmdline([str(wrapper)])):
        raise SystemExit(
            "scheduled task readback does not match the windowless wrapper action; "
            "the registration is not trusted"
        )


def _register_windows_schedule(adapter: dict, command) -> dict:
    pythonw = Path(command[0]).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise SystemExit(f"windowless Python is unavailable at {pythonw}; task was not registered")
    wrapper = _h("_schedule_wrapper")(adapter, command)
    # XML keeps the interpreter and wrapper paths separate, without /TR's
    # command-line length limit or cmd.exe's codepage and metacharacter parsing.
    task = ET.Element("Task", xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task", version="1.2")
    trigger = ET.SubElement(ET.SubElement(task, "Triggers"), "TimeTrigger")
    start = datetime.now() + timedelta(seconds=_h("INTERVAL_SECONDS"))
    ET.SubElement(trigger, "StartBoundary").text = start.isoformat(timespec="seconds")
    ET.SubElement(ET.SubElement(trigger, "Repetition"), "Interval").text = "PT5M"
    principal = ET.SubElement(ET.SubElement(task, "Principals"), "Principal", id="Author")
    ET.SubElement(principal, "LogonType").text = "InteractiveToken"
    settings = ET.SubElement(task, "Settings")
    ET.SubElement(settings, "MultipleInstancesPolicy").text = "IgnoreNew"
    action = ET.SubElement(ET.SubElement(task, "Actions", Context="Author"), "Exec")
    ET.SubElement(action, "Command").text = str(pythonw)
    ET.SubElement(action, "Arguments").text = subprocess.list2cmdline([str(wrapper)])
    definition = wrapper.with_suffix(".xml")
    ET.ElementTree(task).write(definition, encoding="utf-16", xml_declaration=True)
    _h("bounded_run")(
        ["schtasks", "/Create", "/F", "/TN", "MindIEKimiUpdate", "/XML", str(definition)],
        "", timeout=30,
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
        json.dumps({
            "result": "unsupported-platform",
            "hint": f"run every {_h('INTERVAL_SECONDS')}s via your scheduler: " + " ".join(command),
        })
    )
    return 1


def _native_run(argv, timeout):
    completed = subprocess.run(
        argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        errors="replace", timeout=timeout,
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
                plist=plist, detail=detail or bootout_detail,
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
                task=task, detail=detail,
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
        json.dumps({
            "result": "unsupported-platform",
            "hint": "remove the agent.mindie.kimi-update schedule with your platform tools",
        })
    )
    return 1
