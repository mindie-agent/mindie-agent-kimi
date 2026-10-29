from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


def stop_real_services(engine_config, *, timeout=8.0, sleep=time.sleep):
    """Stop a real knowledge service spawned for this fixture's store, then
    wait (bounded) for the store's owner lock to release.

    The whole suite is one owner process, so nothing else makes a spawned
    detached service exit mid-suite; on Windows its open catalog handle pins
    the store files against TemporaryDirectory cleanup, and on POSIX a leak
    pollutes later ports/locks. The stop uses the same core path the adapter
    uses (connect + rpc stop over the fixture's own engine config; the
    consumer.lock release is the core's own "listener and store closed"
    evidence). Fast no-op when no endpoint was ever started. An unconfirmed
    stop or a service that outlives the budget fails loudly — a process is
    never silently leaked.
    """
    from mindie_knowledge.loop import cli as core_cli
    from mindie_knowledge.loop.locks import lock_held

    try:
        config = core_cli.config_at(str(engine_config))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return
    consumer = Path(config["root"]) / config["domain"] / "consumer.lock"
    try:
        connection = core_cli.connect(config)
    except TimeoutError:
        raise
    except (FileNotFoundError, OSError, ValueError):
        return
    try:
        core_cli.rpc(connection, "stop", timeout=2.0)
    except TimeoutError:
        raise
    except Exception as exc:
        raise AssertionError(
            f"knowledge service stop was not confirmed: {type(exc).__name__}"
        ) from None
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not consumer.exists() or lock_held(consumer) is False:
            return
        sleep(0.05)
    raise AssertionError(
        "knowledge service did not release the store within the cleanup budget"
    )


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


import atexit
import shutil
import tempfile

_DIAG_ROOT = Path(tempfile.mkdtemp(prefix="mindie-kimi-test-diag-")).resolve()
atexit.register(lambda: shutil.rmtree(_DIAG_ROOT, ignore_errors=True))

os.environ.setdefault("MINDIE_DIAGNOSTICS_ROOT", str(_DIAG_ROOT / "diag-root"))


def env_for(config=None, extra=None, kimi_home=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    defaults = {
        "HOME": _DIAG_ROOT / "home",
        "USERPROFILE": _DIAG_ROOT / "home",
        "XDG_CONFIG_HOME": _DIAG_ROOT / "config",
        "XDG_DATA_HOME": _DIAG_ROOT / "data",
    }
    for name, value in defaults.items():
        if not extra or name not in extra:
            env[name] = str(value)
    diag_base = Path(config).resolve().parent if config is not None else _DIAG_ROOT
    env.setdefault("MINDIE_DIAGNOSTICS_ROOT", str(diag_base / "diag-root"))
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


def redactor_executable():
    """The installed transcript redactor, resolved once per suite run.

    Tests replace HOME/USERPROFILE per case, so the verified scanner is
    installed under the real user home captured at import time; a per-test
    cache would be deleted by the case's own teardown.
    """
    global _REDACTOR
    if _REDACTOR is None:
        from mindie_knowledge.loop.transcript_redaction import install_scanner

        source = _REAL_HOME / ".cache" / "mindie-agent" / "gitleaks"
        cache = _DIAG_ROOT / "gitleaks"
        if source.is_dir():
            shutil.copytree(source, cache, dirs_exist_ok=True)
        _REDACTOR = install_scanner(cache)
    return _REDACTOR


_REDACTOR = None
_REAL_HOME = Path(os.environ.get("MINDIE_TEST_REAL_HOME") or Path.home())


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
            capture_mode="public-transcript",
            redactor_executable=redactor_executable(),
            summary_command=[sys.executable, str(SCRIPTS / "organizer.py")],
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
            consent_config=str((tmp / "mindie-consent.json").resolve()),
        ),
    )
    if sharing:
        # Capture requires an explicit saved contribution choice (knowledge
        # v0.11 gate); a sharing fixture without one is not configured.
        write_json(
            tmp / "mindie-consent.json",
            {"schema": "mindie-consent/1", "choice": "contribute", "choice_at": 1.0},
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
