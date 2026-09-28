"""Installation probes use the installed dependency without compatibility shims."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import setup


class RuntimeContractTests(unittest.TestCase):
    def probe(self, mutation=""):
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, "-c", mutation + setup.PROBE_SCRIPT],
                                    cwd=directory, env=env, capture_output=True,
                                    text=True, encoding="utf-8", timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [])
            return result.stdout.strip()

    def test_unmodified_installed_runtime_satisfies_probe(self):
        self.assertEqual(self.probe(), "OK")

    def test_missing_consumed_admission_method_is_rejected(self):
        # Deletion is a negative control, never a compatibility addition.
        result = self.probe("from mindie_knowledge.loop.activation import Admission\ndel Admission.inspect\n")
        self.assertTrue(result.startswith("MISSING:"), result)
        self.assertIn("Admission lacks inspect", result)


if __name__ == "__main__":
    unittest.main()
