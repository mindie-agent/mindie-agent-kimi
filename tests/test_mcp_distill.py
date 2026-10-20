"""MCP knowledge-tool argument contract and RPC forwarding.

The native host owns nonce binding, so these tests call ``knowledge_call``
directly with a real admission lease and a fake shared service connection.
They assert the forwarded wire payload; the shared knowledge runtime's own
event and navigation behavior is covered by its component tests.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "tests", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from support import SCRIPTS, make_config

SESSION = "ses_mcp_distill"


class KnowledgeToolSchemaTests(unittest.TestCase):
    def test_new_optional_arguments_are_declared_without_identity_fields(self):
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import mcp_server

        tools = {item["name"]: item for item in mcp_server.KNOWLEDGE_TOOLS}
        query = tools["knowledge_query"]["inputSchema"]
        explain = tools["knowledge_explain"]["inputSchema"]
        feedback = tools["knowledge_feedback"]["inputSchema"]

        self.assertEqual(query["properties"]["navigation_only"]["type"], "boolean")
        self.assertEqual(query["properties"]["run_id"]["type"], "string")
        self.assertEqual(explain["properties"]["run_id"]["type"], "string")
        self.assertEqual(
            feedback["properties"]["evidence_source"]["enum"],
            ["agent_declared", "run_manifest_verified", "ablation_verified"],
        )
        self.assertEqual(query["required"], ["request_nonce"])
        self.assertEqual(explain["required"], ["request_nonce"])
        self.assertEqual(feedback["required"], ["ref", "request_nonce"])
        for schema in (query, explain, feedback):
            self.assertIs(schema["additionalProperties"], False)
            self.assertNotIn("_session_id", schema["properties"])
            self.assertNotIn("_activation", schema["properties"])


class KnowledgeArgumentForwardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        with patch("support.redactor_executable", return_value=sys.executable):
            self.config = make_config(self.root)
        self._saved = {key: os.environ.get(key) for key in ("MINDIE_KIMI_CONFIG",)}
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config)
        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import admission
        import mcp_server

        self.mcp_server = mcp_server
        admission.activate(SESSION, project_root=str(self.root.resolve()))

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def _forward(self, name, args):
        captured = {}

        def fake_rpc(connection, method, payload, *, timeout=None):
            captured.update(method=method, payload=payload, timeout=timeout)
            return {"ok": True}

        with patch(
            "knowledge_service.existing_service",
            return_value={"url": "http://127.0.0.1:1", "token": "synthetic"},
        ), patch("mindie_knowledge.loop.cli.rpc", fake_rpc):
            result = self.mcp_server.knowledge_call(name, args, SESSION)
        self.assertEqual(result, {"ok": True})
        return captured

    def test_query_forwards_navigation_only_and_run_id(self):
        captured = self._forward("knowledge_query", {
            "query": "fixture",
            "navigation_only": True,
            "run_id": "run-query-1",
        })
        self.assertEqual(captured["method"], "query")
        self.assertIs(captured["payload"]["navigation_only"], True)
        self.assertEqual(captured["payload"]["run_id"], "run-query-1")
        self.assertEqual(captured["payload"]["_session_id"], SESSION)
        self.assertIs(captured["payload"]["_session_verified"], True)

    def test_explain_forwards_run_id(self):
        ref = "mindie://vllm-ascend/units/unit-a@1"
        captured = self._forward("knowledge_explain", {
            "ref": ref,
            "run_id": "run-explain-1",
        })
        self.assertEqual(captured["method"], "explain")
        self.assertEqual(captured["payload"]["ref"], ref)
        self.assertEqual(captured["payload"]["run_id"], "run-explain-1")
        self.assertEqual(captured["payload"]["_session_id"], SESSION)

    def test_feedback_forwards_evidence_source(self):
        captured = self._forward("knowledge_feedback", {
            "ref": "mindie://vllm-ascend/units/unit-a@1",
            "event_type": "evaluated",
            "run_id": "run-feedback-1",
            "outcome": "supported",
            "evidence_source": "run_manifest_verified",
        })
        self.assertEqual(captured["method"], "feedback")
        self.assertEqual(captured["payload"]["evidence_source"], "run_manifest_verified")
        self.assertEqual(captured["payload"]["run_id"], "run-feedback-1")
        self.assertEqual(captured["payload"]["outcome"], "supported")
        self.assertEqual(captured["payload"]["_session_id"], SESSION)


if __name__ == "__main__":
    unittest.main()
