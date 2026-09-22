"""Component checks for adapter projection and explicit reporting choice."""
import io
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import diagnostic_support as support


class DiagnosticSupportTests(unittest.TestCase):
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

    def test_shared_configuration_is_explicit_and_no_service_is_started(self):
        calls = []
        integration = types.ModuleType("mindie_diagnostics.integration")
        def configure(enabled, **kw):
            calls.append((enabled, kw))
            return {"enabled": enabled, "worker": {"status": "not_confirmed"}}
        integration.configure_reporting = configure
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"MINDIE_DIAGNOSTICS_CONFIG": str(Path(tmp) / "choice.json")}), patch.dict(sys.modules, {"mindie_diagnostics.integration": integration}):
            enabled = support.configure_reporting(True, sys.executable)
            self.assertTrue(enabled["run_outside_hook"])
            self.assertEqual(enabled["command"], [sys.executable, "-m", "mindie_diagnostics.cli", "reporting", "ensure", "--config", str((Path(tmp) / "choice.json").resolve())])
            disabled = support.configure_reporting(False, sys.executable)
            self.assertFalse(disabled["run_outside_hook"])
            self.assertNotIn("command", disabled)
            self.assertEqual([c[0] for c in calls], [True, False])
            self.assertFalse((Path(tmp) / "choice.json").exists())
            integration.configure_reporting = lambda *args, **kw: {"enabled": False}
            unknown = support.configure_reporting(True, sys.executable)
            self.assertEqual(unknown["status"], "unavailable")
            self.assertNotIn("command", unknown)

    def test_hints_are_registered_native_commands(self):
        hint = support.reporting_hint()
        for action in ("enable", "disable", "status"):
            self.assertEqual(hint[action], "/mindie-agent:reporting-" + action)
