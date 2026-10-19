"""Component checks for adapter projection and explicit reporting choice."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import diagnostic_support as support


class DiagnosticSupportTests(unittest.TestCase):
    def test_missing_runtime_preserves_result_without_shadow_log(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, MINDIE_DIAGNOSTICS_ROOT=str(root / "logs"))
            completed = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-c",
                    "import json,sys; sys.path.insert(0, sys.argv[1]); "
                    "import diagnostic_support as d; "
                    "print(json.dumps(d.attach({'status':'completed','job_id':'keep-job'},"
                    "d.failure('mcp','receipt','storage'))))",
                    str(SCRIPTS),
                ],
                env=env, capture_output=True, text=True, timeout=5, check=True,
            )
            result = json.loads(completed.stdout)
            self.assertEqual(result["status"], "completed")
            self.assertNotIn("isError", result)
            self.assertEqual(result["job_id"], "keep-job")
            self.assertEqual(result["diagnostic"], {"logging_failed": True})
            self.assertIn("diagnostic storage unavailable", result["content"][0]["text"])
            self.assertFalse((root / "logs").exists())

    def test_empty_reference_cannot_suppress_new_failure(self):
        incident = "a" * 32
        for old in ({}, {"logging_failed": False}, {"incident_id": "not-an-id"}):
            result = {"isError": True, "content": [{"type": "text", "text": "original"}],
                      "structuredContent": {"job_id": "keep-job", "diagnostic": old}}
            updated = support.attach(result, {"incident_id": incident, "logging_failed": False})
            self.assertEqual(updated["diagnostic"]["incident_id"], incident)
            self.assertEqual(updated["structuredContent"], result["structuredContent"])
            self.assertEqual(result["content"], [{"type": "text", "text": "original"}])

    def test_trusted_inner_reference_is_not_rewritten(self):
        inner = {"incident_id": "b" * 32, "logging_failed": False}
        result = {"content": [], "structuredContent": {"diagnostic": inner}}
        with patch.object(support, "_record", side_effect=AssertionError("must not record")):
            self.assertEqual(support.failure("mcp", "helper", "protocol",
                                            incident_id=inner["incident_id"]), inner)
            self.assertIs(support.attach(result, {"incident_id": "a" * 32}), result)

    def test_logging_failure_cannot_replace_original_error(self):
        class Broken:
            def write(self, value):
                raise BrokenPipeError("private-error")
        with patch.object(support, "_warned", False), patch.object(support, "_record", side_effect=ImportError("private-error")), patch("sys.stderr", Broken()):
            self.assertEqual(support.failure("mcp", "helper", "protocol"), {"logging_failed": True})

    def test_projection_drops_uncontracted_fields(self):
        raw = {"recorded": True, "incident_id": "a" * 32, "logging_failed": False,
               "secret": "never-export"}
        with patch.object(support, "_record", return_value=lambda *args, **kw: raw):
            self.assertEqual(support.failure("mcp", "helper", "protocol"),
                             {key: raw[key] for key in ("recorded", "incident_id", "logging_failed")})
        with patch.object(support, "_record", return_value=lambda *args, **kw: (True, "a" * 32, False)), patch("sys.stderr", io.StringIO()):
            self.assertEqual(support.failure("mcp", "helper", "protocol"), {"logging_failed": True})
