"""Receipt ownership across concurrent native MCP calls."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import identity
import mcp_server


class BindReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        config = self.root / "kimi.json"
        config.write_text(json.dumps({"state_dir": str(self.root / "state")}))
        self.diagnostics = self.root / "diagnostics"
        environment = patch.dict(os.environ, {
            "MINDIE_KIMI_CONFIG": str(config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.diagnostics),
            "MINDIE_DIAGNOSTICS_CONFIG": str(self.root / "reporting-off.json"),
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(self.close_diagnostics)
        self.session = "ses_receipt_owner"

    def close_diagnostics(self):
        import mindie_diagnostics.logging as diagnostic_logging

        for (_component, location), recorder in list(diagnostic_logging._FAILURE_RECORDERS.items()):
            if Path(location) == self.diagnostics:
                recorder.close()

    def bind(self, key, surface="knowledge"):
        name = "knowledge_query" if surface == "knowledge" else "remote_bash"
        arguments = {"query": "npu"} if surface == "knowledge" else {"command": "true"}
        arguments["request_nonce"] = key
        identity.publish_nonce_bind(
            self.session, key,
            f"mcp__plugin-mindie-agent_{surface}__{name}",
            f"tool-{key}", arguments,
        )
        return {
            "jsonrpc": "2.0", "id": key, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

    def status(self, key):
        with sqlite3.connect(identity.bind_db_path()) as db:
            return db.execute(
                "SELECT status FROM binds WHERE nonce=?", (key,)
            ).fetchone()[0]

    def test_inflight_replay_cannot_finish_owner_receipt(self):
        for succeeds in (True, False):
            with self.subTest(succeeds=succeeds):
                key = f"nonce-receipt-{succeeds}"
                request = self.bind(key)
                entered = threading.Event()
                release = threading.Event()
                calls = []
                payload = {"matches": [{"ref": "task:result", "score": 1.0}]}

                def business_call(name, arguments, session):
                    calls.append((name, arguments, session))
                    entered.set()
                    if not release.wait(timeout=5):
                        raise RuntimeError("test did not release the owner")
                    if not succeeds:
                        raise OSError("knowledge service unavailable")
                    return payload

                with patch.object(mcp_server, "knowledge_call", business_call):
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        owner = executor.submit(mcp_server.handle, "knowledge", request)
                        try:
                            self.assertTrue(entered.wait(timeout=5))
                            duplicate = mcp_server.handle("knowledge", request)
                            self.assertTrue(duplicate["result"]["isError"])
                            self.assertIn("replay rejected", duplicate["result"]["content"][0]["text"])
                            self.assertEqual(self.status(key), "claimed")
                        finally:
                            release.set()
                        response = owner.result(timeout=5)["result"]
                    self.assertEqual(response["isError"], not succeeds)
                    if succeeds:
                        self.assertEqual(response["structuredContent"], payload)
                    else:
                        self.assertIn("knowledge service unavailable", response["content"][0]["text"])
                    expected = "succeeded" if succeeds else "failed"
                    self.assertEqual(self.status(key), expected)
                    replay = mcp_server.handle("knowledge", request)
                    self.assertTrue(replay["result"]["isError"])
                    self.assertEqual(self.status(key), expected)
                self.assertEqual(calls, [("knowledge_query", {"query": "npu"}, self.session)])

    def test_accounting_failure_preserves_result_and_reports_incident(self):
        from mindie_diagnostics.local_snapshot import recent_failures

        scenarios = [
            ("begin", "knowledge", "success"),
            ("update", "knowledge", "success"),
            ("commit", "knowledge", "success"),
            ("open", "knowledge", "success"),
            ("update", "remote", "success"),
            ("update", "remote", "failed"),
            ("update", "remote", "exception"),
        ]
        for phase, surface, outcome in scenarios:
            with self.subTest(phase=phase, surface=surface, outcome=outcome):
                key = f"nonce-{phase}-{surface}-{outcome}"
                request = self.bind(key, surface)
                business_done = False
                attempts = []
                connect = identity._connect
                payload = {"outcome": outcome, "job_id": "job-receipt-1"}

                def business_call(*_args):
                    nonlocal business_done
                    business_done = True
                    attempts.append(key)
                    if outcome == "exception":
                        raise OSError("remote connection lost")
                    return payload if surface == "knowledge" else {"text": "original remote result", "result": payload}

                def accounting_connection(path, *, create):
                    if business_done and phase == "open":
                        raise OSError("receipt storage unavailable")
                    connection = connect(path, create=create)
                    if business_done:
                        def authorize(action, first, _second, _database, _trigger):
                            denied = (
                                phase == "update" and action == sqlite3.SQLITE_UPDATE
                                or phase in {"begin", "commit"}
                                and action == sqlite3.SQLITE_TRANSACTION and first == phase.upper()
                            )
                            return sqlite3.SQLITE_DENY if denied else sqlite3.SQLITE_OK
                        connection.set_authorizer(authorize)
                    return connection

                with patch.object(identity, "_connect", accounting_connection):
                    with patch.object(mcp_server, f"{surface}_call", business_call):
                        result = mcp_server.handle(surface, request)["result"]
                self.assertEqual(result["isError"], outcome != "success")
                structured = result["structuredContent"]
                self.assertIn("bind_warning", structured)
                diagnostic = structured["diagnostic_ref"]
                self.assertFalse(diagnostic["logging_failed"])
                incident = diagnostic["incident_id"]
                recorded = recent_failures([str(self.diagnostics)])["recent"]
                self.assertTrue(any(row["incident_id"] == incident for row in recorded))
                if outcome != "exception":
                    for field, value in payload.items():
                        self.assertEqual(structured[field], value)
                    original_text = mcp_server.canonical(payload) if surface == "knowledge" else "original remote result"
                    self.assertEqual(result["content"][0]["text"], original_text)
                else:
                    self.assertEqual(structured["stage"], "helper_failed")
                    self.assertEqual(structured["remote_outcome"], "unconfirmed")
                self.assertTrue(any("receipt" in item["text"].lower() for item in result["content"][1:]))
                self.assertEqual(self.status(key), "claimed")
                replay = mcp_server.handle(surface, request)["result"]
                self.assertTrue(replay["isError"])
                self.assertNotIn("bind_warning", replay.get("structuredContent", {}))
                self.assertEqual(self.status(key), "claimed")
                self.assertEqual(attempts, [key])

    def test_unavailable_diagnostics_keeps_success_and_warning_visible(self):
        import diagnostic_support

        request = self.bind("nonce-log-unavailable")
        with sqlite3.connect(identity.bind_db_path()) as db:
            db.execute("""
                CREATE TRIGGER fail_receipt BEFORE UPDATE OF status ON binds
                WHEN NEW.status = 'succeeded'
                BEGIN SELECT RAISE(FAIL, 'private-storage-detail'); END
            """)
        payload = {"matches": [{"ref": "task:keep"}]}
        with patch.object(mcp_server, "knowledge_call", return_value=payload):
            with patch.object(diagnostic_support, "_record", side_effect=ImportError("unavailable")):
                result = mcp_server.handle("knowledge", request)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["structuredContent"]["matches"], payload["matches"])
        self.assertEqual(result["structuredContent"]["diagnostic_ref"], {"logging_failed": True})
        self.assertIn("bind_warning", result["structuredContent"])
        self.assertNotIn("private-storage-detail", json.dumps(result))

    def test_missing_receipt_store_is_reported_without_repeating_business(self):
        request = self.bind("receipt-store-removed")
        payload = {"matches": [{"ref": "task:completed"}]}
        calls = []

        def business_call(*_args):
            calls.append("completed")
            identity.bind_db_path().unlink()
            return payload

        with patch.object(mcp_server, "knowledge_call", business_call):
            result = mcp_server.handle("knowledge", request)["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(result["structuredContent"]["matches"], payload["matches"])
            self.assertIn("bind_warning", result["structuredContent"])
            self.assertFalse(result["structuredContent"]["diagnostic_ref"]["logging_failed"])
            replay = mcp_server.handle("knowledge", request)["result"]
            self.assertTrue(replay["isError"])
        self.assertEqual(calls, ["completed"])
        self.assertFalse(identity.bind_db_path().exists())
