import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import bounded  # noqa: E402


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


class BoundedTests(unittest.TestCase):
    def test_timeout_kills_process_group(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            bounded.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "",
                timeout=0.4,
            )
        self.assertLess(time.monotonic() - started, 3)

    def test_output_bound_kills_runaway_during_execution(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            bounded.run(
                [sys.executable, "-c",
                 "import sys\nwhile True: sys.stdout.write('x' * 65536); "
                 "sys.stdout.flush()"],
                "",
                timeout=30,
                max_output=128 * 1024,
            )
        self.assertIn("output exceeds the bound", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 10)

    def test_timeout_kills_spawned_child_tree(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError):
            bounded.run(
                [sys.executable, "-c",
                 "import subprocess, sys, time\n"
                 "subprocess.Popen([sys.executable, '-c', "
                 "'import time; time.sleep(30)'])\n"
                 "time.sleep(30)"],
                "",
                timeout=0.5,
            )
        self.assertLess(time.monotonic() - started, 5)

    def test_non_reading_stdin_does_not_hang_parent(self):
        started = time.monotonic()
        with self.assertRaises(RuntimeError) as ctx:
            bounded.run(
                [sys.executable, "-c", "import time; time.sleep(30)"],
                "x" * 100000,
                timeout=0.5,
            )
        self.assertIn("timed out", str(ctx.exception))
        self.assertLess(time.monotonic() - started, 3)

    def test_timeout_reaps_grandchild_that_would_survive_parent(self):
        with tempfile.TemporaryDirectory() as raw:
            pidfile = Path(raw) / "pids"
            code = (
                "import os, subprocess, sys, time\n"
                f"pidfile = {str(pidfile)!r}\n"
                "child = subprocess.Popen(\n"
                "    [sys.executable, '-c', 'import time; time.sleep(30)'],\n"
                "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL)\n"
                "open(pidfile, 'w').write(f'{os.getpid()} {child.pid}')\n"
                "time.sleep(30)\n"
            )
            started = time.monotonic()
            with self.assertRaises(RuntimeError):
                bounded.run([sys.executable, "-c", code], "", timeout=0.6)
            self.assertLess(time.monotonic() - started, 3)
            pids = pidfile.read_text().split()
            self.assertEqual(len(pids), 2)
            for pid in pids:
                self.assertTrue(_wait_dead(int(pid)), f"pid {pid} still alive")

    def test_success_closes_owned_tree(self):
        with tempfile.TemporaryDirectory() as raw:
            pidfile = Path(raw) / "g.pid"
            code = (
                "import subprocess, sys\n"
                f"pidfile = {str(pidfile)!r}\n"
                "child = subprocess.Popen(\n"
                "    [sys.executable, '-c', 'import time; time.sleep(30)'],\n"
                "    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,\n"
                "    stderr=subprocess.DEVNULL)\n"
                "open(pidfile, 'w').write(str(child.pid))\n"
                "print('ok', flush=True)\n"
            )
            out = bounded.run([sys.executable, "-c", code], "", timeout=5)
            self.assertEqual(out.strip(), "ok")
            pid = int(pidfile.read_text().strip())
            self.assertTrue(_wait_dead(pid), f"grandchild {pid} survived success")

    def test_check_false_returns_stdout_on_nonzero(self):
        out = bounded.run(
            [
                sys.executable,
                "-c",
                "import sys; print('payload', flush=True); raise SystemExit(2)",
            ],
            "",
            timeout=3,
            check=False,
        )
        self.assertEqual(out.strip(), "payload")

    def test_cwd_is_honored(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            out = bounded.run(
                [sys.executable, "-c",
                 "open('here.txt', 'w').write('ok'); print('done')"],
                "",
                timeout=3,
                cwd=str(tmp),
            )
            self.assertEqual(out.strip(), "done")
            self.assertEqual((tmp / "here.txt").read_text(), "ok")
