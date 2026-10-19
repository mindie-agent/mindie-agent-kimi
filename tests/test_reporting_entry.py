"""Native choice controls both sharing and real diagnostic queue admission."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SCRIPTS, close_diagnostic_writers, make_config, plugin_origin, turn_records, write_session
sys.path.insert(0, str(SCRIPTS))
import entry
import diagnostic_support
from mindie_knowledge import consent_store
from mindie_diagnostics.ingestion import ingest
from mindie_diagnostics.outbox import Outbox


class ReportingEntryTests(unittest.TestCase):
    def test_native_entry_migrates_legacy_contribution_only_on_explicit_choice(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config = make_config(root, sharing=True)
            home = root / "kimi-home"
            authority = config.parent / "mindie-consent.json"
            authority.write_text(json.dumps({
                "schema": "mindie-consent/1", "choice": "contribute",
                "reporting": "disabled",
            }))
            before = authority.read_bytes()
            with patch.dict(os.environ, MINDIE_KIMI_CONFIG=str(config), KIMI_CODE_HOME=str(home)), \
                    patch("mindie_diagnostics.reporting_commands.ensure", return_value={
                        "status": "degraded", "category": "test_service_unavailable",
                    }) as ensure, patch("knowledge_service.ensure_service"):
                write_session(home, "ses_migrate", turn_records(
                    plugin_origin("init", "legacy-startup"), "init"), cwd=root)
                result = entry.dispatch("ses_migrate", str(root), "init")
                self.assertEqual(result["reporting_activation"]["status"], "needs_choice")
                self.assertEqual(authority.read_bytes(), before)
                ensure.assert_not_called()

                arguments = "contribute owner/repo owner"
                write_session(home, "ses_migrate", turn_records(
                    plugin_origin("init", "explicit-migration", arguments), arguments), cwd=root)
                result = entry.dispatch("ses_migrate", str(root), "init")
                self.assertEqual(result["sharing"]["reporting"]["status"], "degraded")
                revision = consent_store.read(authority)["choice_revision"]
                self.assertTrue(revision)
                ensure.assert_called_once()

                write_session(home, "ses_migrate", turn_records(
                    plugin_origin("init", "next-startup"), "init"), cwd=root)
                result = entry.dispatch("ses_migrate", str(root), "init")
                self.assertEqual(result["first_use"], "contribute")
                self.assertEqual(result["reporting_activation"]["status"], "degraded")
                self.assertEqual(consent_store.read(authority)["choice_revision"], revision)

    def test_explicit_init_choice_does_not_start_reporter_twice(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config = make_config(root)
            home = root / "kimi-home"
            arguments = "contribute account/knowledge account"
            write_session(home, "ses_init", turn_records(
                plugin_origin("init", "one-contribution-choice", arguments), arguments), cwd=root)
            with patch.dict(os.environ, MINDIE_KIMI_CONFIG=str(config), KIMI_CODE_HOME=str(home)), \
                    patch("mindie_diagnostics.reporting_commands.ensure", return_value={
                        "status": "degraded", "category": "test_service_unavailable",
                    }) as ensure, patch("knowledge_service.ensure_service"):
                result = entry.dispatch("ses_init", str(root), "init")
                self.assertEqual(result["sharing"]["reporting"]["status"], "degraded")
                ensure.assert_called_once()

    def test_native_contribution_is_once_and_real_failure_reaches_profile_queue(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config = make_config(root)
            logs = root / "logs"
            home = root / "kimi-home"
            arguments = "--repository account/knowledge --account account --visibility public"
            write_session(home, "ses_report", turn_records(
                plugin_origin("sharing-enable", "contribute-on", arguments), "sharing-enable " + arguments), cwd=root)
            with patch.dict(os.environ, MINDIE_KIMI_CONFIG=str(config), KIMI_CODE_HOME=str(home),
                            MINDIE_DIAGNOSTICS_ROOT=str(logs),
                            MINDIE_DIAGNOSTICS_CONFIG=str(root / "wrong-policy.json")), \
                    patch("mindie_diagnostics.reporting_commands.ensure", return_value={
                        "status": "degraded", "category": "test_service_unavailable",
                    }) as ensure:
                result = entry.dispatch("ses_report", str(root), "sharing-enable")
                self.assertEqual(result["reporting"]["status"], "degraded")
                self.assertTrue(entry.dispatch("ses_report", str(root), "sharing-enable")["already"])
                ensure.assert_called_once_with(config.parent / "diagnostics.json", unit_dir=None)
                diagnostic = diagnostic_support.failure("mcp", "tool", "internal", revision="a" * 40,
                                                        exception=RuntimeError("synthetic public fixture"))
                self.assertFalse(diagnostic["logging_failed"])
                queue = Outbox(root / "queue.sqlite3")
                self.assertEqual(ingest(logs, queue, config=config.parent / "diagnostics.json")["enqueued"], 1)
                self.assertEqual(queue.rows()[0]["incident_ids"], [diagnostic["incident_id"]])
                entry._apply_choice("ses_report", str(root), "disabled")
                self.assertEqual(queue.withdraw_unconsented(), 1)
                self.assertEqual(ensure.call_count, 1)
                close_diagnostic_writers(logs)

    def test_model_arguments_cannot_authorize_contribution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "kimi-home"
            write_session(home, "ses_report", turn_records({"kind": "user"}, "sharing-enable"), cwd=root)
            with patch.dict(os.environ, MINDIE_KIMI_CONFIG=str(root / "missing"), KIMI_CODE_HOME=str(home)), \
                    patch("mindie_diagnostics.reporting_commands.ensure") as ensure:
                with self.assertRaises(ValueError):
                    entry.dispatch("ses_report", str(root), "sharing-enable", arguments="--repository account/knowledge --account account --visibility public")
                ensure.assert_not_called()
                self.assertFalse((root / "mindie-consent.json").exists())

    def test_startup_preserves_legacy_choice_and_explicit_reconfirmation_is_required(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config = make_config(root)
            authority = config.parent / "mindie-consent.json"
            with patch.dict(os.environ, MINDIE_KIMI_CONFIG=str(config)), \
                    patch("mindie_diagnostics.reporting_commands.ensure") as ensure:
                for choice in ("contribute", "disabled", "later", "read-only"):
                    authority.write_text(json.dumps({"schema": "mindie-consent/1", "choice": choice,
                                                     "reporting": "disabled"}))
                    before = authority.read_bytes()
                    result = diagnostic_support.activate_reporting()
                    self.assertEqual(result["status"], "needs_choice")
                    self.assertFalse(result["enabled"])
                    self.assertEqual(authority.read_bytes(), before)
                ensure.assert_not_called()
                consent_store.record_choice(authority, "disabled")
                self.assertEqual(diagnostic_support.activate_reporting()["status"], "disabled")
