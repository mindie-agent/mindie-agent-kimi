import os
import sys
import time
import unittest

from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import bounded  # noqa: E402


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
