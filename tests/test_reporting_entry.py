"""Native-origin fixtures: component authorization, not host acceptance."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SCRIPTS, make_config, plugin_origin, turn_records, write_session
sys.path.insert(0, str(SCRIPTS))
import entry
import diagnostic_support


class ReportingEntryTests(unittest.TestCase):
    def test_native_choice_is_once_and_independent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = make_config(root)
            home = root / "kimi-home"
            write_session(home, "ses_report", turn_records(plugin_origin("reporting-enable", "report-on"), "reporting-enable"), cwd=root)
            with patch.dict(os.environ, {"MINDIE_KIMI_CONFIG": str(config), "KIMI_CODE_HOME": str(home), "XDG_CONFIG_HOME": str(root / "xdg")}):
                with patch.object(diagnostic_support, "configure_reporting", return_value={"enabled": True}) as configure, patch.object(diagnostic_support, "reporting_status", return_value={"enabled": True}):
                    self.assertTrue(entry.dispatch("ses_report", str(root), "reporting-enable")["enabled"])
                    self.assertTrue(entry.dispatch("ses_report", str(root), "reporting-enable")["already"])
                    configure.assert_called_once_with(True, sys.executable)
                from admission import gate
                self.assertIsNone(gate().active_lease("ses_report"))
                from sharing import public_status
                self.assertFalse(public_status()["enabled"])

    def test_model_arguments_cannot_authorize_enable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "kimi-home"
            write_session(home, "ses_report", turn_records({"kind": "user"}, "reporting-enable"), cwd=root)
            with patch.dict(os.environ, {"MINDIE_KIMI_CONFIG": str(root / "missing"), "KIMI_CODE_HOME": str(home), "XDG_CONFIG_HOME": str(root / "xdg")}), patch.object(diagnostic_support, "configure_reporting") as configure:
                with self.assertRaises(ValueError):
                    entry.dispatch("ses_report", str(root), "reporting-enable", arguments="enable yes")
                configure.assert_not_called()
