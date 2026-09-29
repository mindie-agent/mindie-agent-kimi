from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


def deny_read(path):
    if os.name != "nt":
        path.chmod(0)
        return
    import csv
    sid = list(csv.reader(subprocess.check_output(
        ["whoami", "/user", "/fo", "csv", "/nh"], text=True).splitlines()))[0][1]
    if not sid.startswith("S-1-"):
        raise AssertionError("current user SID unavailable")
    subprocess.run(["icacls", str(path), "/deny", f"*{sid}:(RD)"],
                   check=True, capture_output=True)


def allow_read(path):
    if os.name != "nt":
        path.chmod(0o600)
        return
    import csv
    sid = list(csv.reader(subprocess.check_output(
        ["whoami", "/user", "/fo", "csv", "/nh"], text=True).splitlines()))[0][1]
    subprocess.run(["icacls", str(path), "/remove:d", "*" + sid],
                   check=True, capture_output=True)


def windows_process_alive(pid):
    """Observe without sending a signal; os.kill(pid, 0) kills on Windows."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        if ctypes.get_last_error() == 87:
            return False
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        state = kernel.WaitForSingleObject(handle, 0)
        if state not in (0, 258):
            raise ctypes.WinError(ctypes.get_last_error())
        return state == 258
    finally:
        kernel.CloseHandle(handle)


def close_diagnostic_writers(root):
    """Release only this fixture's cached log writers before deleting its root.

    Real hooks exit their process. In-process hook fixtures must explicitly
    end that resource lifetime; Windows cannot unlink an open log file.
    """
    if not root:
        return
    import mindie_diagnostics.logging as diagnostic_logging

    selected = Path(root).absolute()
    for (_component, location), recorder in list(diagnostic_logging._FAILURE_RECORDERS.items()):
        if Path(location) == selected:
            recorder.close()


def env_for(config=None, extra=None, kimi_home=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if config is not None:
        env["MINDIE_KIMI_CONFIG"] = str(config)
    if kimi_home is not None:
        env["KIMI_CODE_HOME"] = str(kimi_home)
    if extra:
        env.update(extra)
    return env


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


def make_config(tmp: Path, *, sharing=False, roots=None):
    config = tmp / "kimi.json"
    engine = tmp / "kimi.engine.json"
    community = tmp / "kimi.community.json"
    root = tmp / "domain"
    admission = root / "admission.sqlite3"
    write_json(
        engine,
        dict(
            root=str(root),
            domain="vllm-ascend",
            admission_path=str(admission),
            transcript_adapter=str(SCRIPTS / "transcript.py"),
            agent_command=[sys.executable, str(SCRIPTS / "organizer.py")],
            community_config=str(community),
        ),
    )
    write_json(
        config,
        dict(
            python=sys.executable,
            engine_config=str(engine),
            community_config=str(community),
            state_dir=str(tmp / "adapter-state"),
        ),
    )
    write_json(
        community,
        dict(
            schema="mindie-community-config/1",
            enabled=bool(sharing),
            generation="gen-test" if sharing else "off",
            enabled_at=1.0 if sharing else None,
            repository="owner/repo",
            branch="main",
            project_roots=[str(Path(p).resolve()) for p in (roots or ([tmp] if sharing else []))],
            idle_seconds=300,
            visibility="public",
        ),
    )
    return config


def run_bridge(op, event, config, *, kimi_home=None, timeout=8):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "bridge.py"), op],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env_for(config, kimi_home=kimi_home),
        cwd=str(ROOT),
    )


def plugin_origin(command, activation_id, args=""):
    return dict(
        kind="plugin_command",
        pluginId="mindie-agent",
        commandName=command,
        commandArgs=args,
        activationId=activation_id,
        trigger="user-slash",
    )


def turn_records(origin, text="cmd", time=2):
    return [
        dict(
            type="turn.prompt",
            origin=origin,
            input=[dict(type="text", text=text)],
            time=time,
        ),
        dict(
            type="context.append_message",
            message=dict(
                role="user",
                content=[dict(type="text", text=text)],
                origin=origin,
            ),
            time=time,
        ),
    ]


def write_session(home: Path, session_id: str, records, *, workdir="wd_test_abc", state=None, cwd=None):
    root = home / "sessions" / workdir / session_id
    wire = root / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text("".join(json.dumps(row) + "\n" for row in records))
    payload = dict(title="t", createdAt=1000, cwd=str(cwd or home.resolve()))
    if state:
        payload.update(state)
    write_json(root / "state.json", payload)
    with (home / "session_index.jsonl").open("a") as stream:
        stream.write(
            json.dumps(dict(sessionId=session_id, sessionDir=str(root), workDir="/tmp/proj"))
            + "\n"
        )
    return root, wire
