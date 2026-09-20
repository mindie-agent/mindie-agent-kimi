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
