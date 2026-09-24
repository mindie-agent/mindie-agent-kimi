import json
import os
import selectors
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import ROOT, SCRIPTS, env_for, make_config, write_json

LAUNCH = SCRIPTS / "mindie_launch.py"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "state="],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False
    state = result.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _wait_dead(pid: int, timeout=2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


def _state_dir(config: Path) -> Path:
    return Path(json.loads(config.read_text())["state_dir"])


def write_current(config: Path, generation: Path, python=None, sha="a" * 40):
    state = _state_dir(config)
    path = state / "update" / "current.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        path,
        {
            "generation": str(generation),
            "python": python or sys.executable,
            "adapter_config": str(config),
            "sha": sha,
        },
    )
    return state


def write_stub_generation(root: Path, **scripts):
    scripts_dir = root / "scripts"
    scripts_dir.mkdir(parents=True)
    for name, text in scripts.items():
        (scripts_dir / name).write_text(text)
    return root


def run_launch(args, stdin="", *, env, timeout=3):
    return subprocess.run(
        [sys.executable, str(LAUNCH), *args],
        input=stdin,
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )


STOP_EVENT = json.dumps(
    {
        "hook_event_name": "Stop",
        "session_id": "ses_stop",
        "cwd": "/tmp",
        "stop_hook_active": False,
    }
)


class LauncherTests(unittest.TestCase):
    def test_sharing_off_stop_creates_no_update_state(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=False)
            env = env_for(config)
            started = time.monotonic()
            result = run_launch(["hook", "stop"], STOP_EVENT, env=env)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            state = _state_dir(config)
            self.assertFalse(state.exists())
            self.assertFalse((state / "update" / "operation.lock").exists())

    def test_unconfigured_stop_creates_no_state(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            missing = tmp / "absent.json"
            xdg = tmp / "xdg"
            xdg.mkdir()
            env = env_for(
                extra={
                    "MINDIE_KIMI_CONFIG": str(missing),
                    "XDG_CONFIG_HOME": str(xdg),
                }
            )
            started = time.monotonic()
            result = run_launch(["hook", "stop"], STOP_EVENT, env=env)
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertEqual(list(xdg.rglob("*")), [])

    def _popen_hook(self, args, env):
        return subprocess.Popen(
            [sys.executable, str(LAUNCH), *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    def test_abnormal_hook_child_fails_open_without_forwarding_its_output(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=True, roots=[tmp])
            generation = write_stub_generation(
                tmp / "gen",
                **{
                    "bridge.py": (
                        "import sys\n"
                        "print(sys.stdin.read().strip() or '{}', flush=True)\n"
                        "raise SystemExit(2)\n"
                    )
                },
            )
            write_current(config, generation)
            result = run_launch(
                ["hook", "stop"],
                json.dumps({"ok": True, "n": 1}),
                env=env_for(config),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})

    def test_hook_writer_keeps_pipe_open_returns_empty_under_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=True, roots=[tmp])
            pidfile = tmp / "child.pid"
            generation = write_stub_generation(
                tmp / "gen",
                **{
                    "bridge.py": (
                        "import os, sys, time\n"
                        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                        "sys.stdin.read()\n"
                        "print('should-not-run', flush=True)\n"
                    )
                },
            )
            write_current(config, generation)
            started = time.monotonic()
            proc = self._popen_hook(["hook", "stop"], env_for(config))
            try:
                proc.stdin.write("x")
                proc.stdin.flush()
                proc.wait(timeout=4)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 2)
                self.assertEqual(proc.returncode, 0, proc.stderr.read())
                self.assertEqual(json.loads(proc.stdout.read()), {})
                if pidfile.exists():
                    self.assertTrue(
                        _wait_dead(int(pidfile.read_text())),
                        "hook child survived stdin-budget miss",
                    )
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    def test_sharing_off_stop_returns_before_stdin(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=False)
            started = time.monotonic()
            proc = self._popen_hook(["hook", "stop"], env_for(config))
            try:
                proc.stdin.write("x")
                proc.stdin.flush()
                proc.wait(timeout=2)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 1.0)
                self.assertEqual(proc.returncode, 0, proc.stderr.read())
                self.assertEqual(json.loads(proc.stdout.read()), {})
                state = _state_dir(config)
                self.assertFalse(state.exists())
                self.assertFalse((state / "update" / "operation.lock").exists())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    def test_unconfigured_stop_returns_before_stdin(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            missing = tmp / "absent.json"
            xdg = tmp / "xdg"
            xdg.mkdir()
            env = env_for(
                extra={
                    "MINDIE_KIMI_CONFIG": str(missing),
                    "XDG_CONFIG_HOME": str(xdg),
                }
            )
            started = time.monotonic()
            proc = self._popen_hook(["hook", "stop"], env)
            try:
                proc.stdin.write("x")
                proc.stdin.flush()
                proc.wait(timeout=2)
                elapsed = time.monotonic() - started
                self.assertLess(elapsed, 1.0)
                self.assertEqual(proc.returncode, 0, proc.stderr.read())
                self.assertEqual(json.loads(proc.stdout.read()), {})
                self.assertEqual(list(xdg.rglob("*")), [])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=2)
                try:
                    proc.stdin.close()
                except Exception:
                    pass

    def test_non_reading_hook_child_is_reaped_under_2s(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=True, roots=[tmp])
            pidfile = tmp / "child.pid"
            generation = write_stub_generation(
                tmp / "gen",
                **{
                    "bridge.py": (
                        "import os, time\n"
                        f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                        "time.sleep(30)\n"
                    )
                },
            )
            write_current(config, generation)
            started = time.monotonic()
            result = run_launch(
                ["hook", "stop"],
                STOP_EVENT + "x" * 8000,
                env=env_for(config),
                timeout=4,
            )
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            pid = int(pidfile.read_text())
            self.assertTrue(_wait_dead(pid), f"hook child {pid} still alive")

    @unittest.skipUnless(os.name == "posix", "fcntl flock")
    def test_lock_contended_hook_returns_under_2s(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=True, roots=[tmp])
            state = write_current(config, tmp / "unused-gen")
            lock_path = state / "update" / "operation.lock"
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import fcntl, os, time, sys\n"
                    f"path = {str(lock_path)!r}\n"
                    "os.makedirs(os.path.dirname(path), exist_ok=True)\n"
                    "fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)\n"
                    "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                    "print('held', flush=True)\n"
                    "time.sleep(4)\n",
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                started = time.monotonic()
                result = run_launch(
                    ["hook", "stop"], STOP_EVENT, env=env_for(config)
                )
                self.assertLess(time.monotonic() - started, 2)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})
            finally:
                holder.terminate()
                holder.wait(timeout=5)

    @unittest.skipUnless(os.name == "posix", "fcntl flock")
    def test_tools_call_holds_lock_until_child_exits(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            hold = tmp / "hold"
            release = tmp / "release"
            generation = write_stub_generation(
                tmp / "gen",
                **{
                    "mcp_server.py": (
                        "import json, os, sys, time\n"
                        "from pathlib import Path\n"
                        f"Path({str(hold)!r}).write_text(str(os.getpid()))\n"
                        "deadline = time.time() + 5\n"
                        f"release = Path({str(release)!r})\n"
                        "while time.time() < deadline and not release.exists():\n"
                        "    time.sleep(0.05)\n"
                        "msg = json.loads(sys.stdin.read())\n"
                        "print(json.dumps({'jsonrpc':'2.0','id':msg.get('id'),"
                        "'result':{'tools':[]}}), flush=True)\n"
                    )
                },
            )
            state = write_current(config, generation)
            env = env_for(
                config, extra={"PYTHONPATH": "/tmp/should-not-leak"}
            )
            proc = subprocess.Popen(
                [sys.executable, str(LAUNCH), "mcp", "knowledge"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            try:
                proc.stdin.write(
                    json.dumps(
                        dict(jsonrpc="2.0", id=1, method="initialize", params={})
                    )
                    + "\n"
                )
                proc.stdin.flush()
                init = json.loads(proc.stdout.readline())
                self.assertEqual(
                    init["result"]["serverInfo"]["name"], "mindie-kimi-front"
                )
                proc.stdin.write(
                    json.dumps(dict(jsonrpc="2.0", id=2, method="tools/list"))
                    + "\n"
                )
                proc.stdin.flush()
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and not hold.exists():
                    time.sleep(0.05)
                self.assertTrue(hold.exists(), proc.stderr.read() if proc.poll() else "")
                lock_path = state / "update" / "operation.lock"
                check = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import fcntl, os, sys\n"
                        f"fd = os.open({str(lock_path)!r}, os.O_RDWR)\n"
                        "try:\n"
                        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                        "    print('unlocked')\n"
                        "except OSError:\n"
                        "    print('held')\n",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                self.assertEqual(check.stdout.strip(), "held")
                child_pid = int(hold.read_text())
                release.write_text("go")
                listed = json.loads(proc.stdout.readline())
                self.assertEqual(listed["id"], 2)
                self.assertIn("tools", listed["result"])
                self.assertTrue(_wait_dead(child_pid), f"mcp child {child_pid} survived")
            finally:
                if not release.exists():
                    release.write_text("go")
                proc.stdin.close()
                proc.kill()
                proc.wait(timeout=2)

    def test_updater_mode_selects_tuple_without_holding_lock(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            gen_adapter = tmp / "gen-adapter.json"
            gen_adapter.write_text(config.read_text())
            generation = write_stub_generation(
                tmp / "gen",
                **{
                    "updater.py": (
                        "import fcntl, json, os, sys\n"
                        "from pathlib import Path\n"
                        "cfg = json.loads(Path(os.environ['MINDIE_KIMI_CONFIG']).read_text())\n"
                        "lock = Path(cfg['state_dir']) / 'update' / 'operation.lock'\n"
                        "lock.parent.mkdir(parents=True, exist_ok=True)\n"
                        "fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)\n"
                        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                        "print(json.dumps({\n"
                        "    'op': sys.argv[1],\n"
                        "    'config': os.environ.get('MINDIE_KIMI_CONFIG'),\n"
                        "    'pythonpath': os.environ.get('PYTHONPATH'),\n"
                        "    'got_lock': True,\n"
                        "}))\n"
                    )
                },
            )
            state = _state_dir(config)
            (state / "update").mkdir(parents=True)
            write_json(
                state / "update" / "current.json",
                {
                    "generation": str(generation),
                    "python": sys.executable,
                    "adapter_config": str(gen_adapter),
                    "sha": "b" * 40,
                },
            )
            env = env_for(config, extra={"PYTHONPATH": "/tmp/should-not-leak"})
            result = run_launch(["updater", "status"], env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["op"], "status")
            self.assertEqual(payload["config"], str(gen_adapter))
            self.assertIsNone(payload["pythonpath"])
            self.assertTrue(payload["got_lock"])

    def test_explicit_config_flag_does_not_need_env(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=False)
            xdg = tmp / "xdg"
            xdg.mkdir()
            env = env_for(extra={"XDG_CONFIG_HOME": str(xdg)})
            env.pop("MINDIE_KIMI_CONFIG", None)
            result = run_launch(
                ["--config", str(config), "hook", "stop"],
                STOP_EVENT,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertEqual(list(xdg.rglob("*")), [])
            write_current(config, ROOT)
            generation = write_stub_generation(
                tmp / "gen2",
                **{
                    "mcp_server.py": (
                        "import json, sys\n"
                        "print(json.dumps({"
                        "'jsonrpc':'2.0','id':1,"
                        "'result':{'tools':[{'name':'from-custom'}]}}))\n"
                    )
                },
            )
            write_current(config, generation)
            # Keep the MCP transport open until the response; EOF cancels work.
            listed = subprocess.Popen(
                [sys.executable, str(LAUNCH), "--config", str(config), "mcp", "knowledge"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=env,
            )
            selector = selectors.DefaultSelector()
            try:
                listed.stdin.write(json.dumps(dict(jsonrpc="2.0", id=1, method="tools/list")) + "\n")
                listed.stdin.flush()
                selector.register(listed.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(3), "MCP response deadline")
                payload = json.loads(listed.stdout.readline())
                self.assertEqual(payload["result"]["tools"][0]["name"], "from-custom")
                listed.stdin.close()
                self.assertEqual(listed.wait(timeout=3), 0)
            finally:
                selector.close()
                if listed.poll() is None:
                    listed.kill()
                    listed.wait(timeout=3)
                for stream in (listed.stdin, listed.stdout, listed.stderr):
                    stream.close()

    def test_front_dispatches_once_to_real_generation_scripts(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            write_current(config, ROOT)
            env = env_for(config, extra={"PYTHONPATH": "/tmp/should-not-leak"})
            proc = subprocess.Popen(
                [sys.executable, str(LAUNCH), "mcp", "knowledge"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            )
            try:
                proc.stdin.write(
                    json.dumps(
                        dict(jsonrpc="2.0", id=1, method="initialize", params={})
                    )
                    + "\n"
                )
                proc.stdin.flush()
                init = json.loads(proc.stdout.readline())
                self.assertEqual(init["id"], 1)
                proc.stdin.write(
                    json.dumps(dict(jsonrpc="2.0", id="list-a", method="tools/list"))
                    + "\n"
                )
                proc.stdin.flush()
                listed = json.loads(proc.stdout.readline())
                self.assertEqual(listed["id"], "list-a")
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("mindie_entry", names)
            finally:
                proc.stdin.close()
                proc.kill()
                proc.wait(timeout=2)


def _diag_events(root: Path):
    events = []
    for path in Path(root).rglob("*.jsonl"):
        try:
            events.extend(path.read_text().splitlines())
        except OSError:
            pass
    return events


class LauncherEntryDiagnosticTests(unittest.TestCase):
    def test_malformed_adapter_config_is_recorded_not_off(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            config.write_text("{ not json")
            env = env_for(config, extra={"MINDIE_DIAGNOSTICS_ROOT": str(diag)})
            result = run_launch(["hook", "stop"], STOP_EVENT, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            events = _diag_events(diag)
            self.assertTrue(any("configuration" in event for event in events), events)

    def test_malformed_community_config_is_recorded_not_off(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            community = tmp / "kimi.community.json"
            community.write_text("{ not json")
            env = env_for(config, extra={"MINDIE_DIAGNOSTICS_ROOT": str(diag)})
            result = run_launch(["hook", "stop"], STOP_EVENT, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            events = _diag_events(diag)
            self.assertTrue(any("configuration" in event for event in events), events)

    def test_explicit_sharing_off_stays_quiet(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=False)
            env = env_for(config, extra={"MINDIE_DIAGNOSTICS_ROOT": str(diag)})
            result = run_launch(["hook", "stop"], STOP_EVENT, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertEqual(_diag_events(diag), [])
