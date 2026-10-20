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
