"""Native Kimi entry routing across independent knowledge domains.

Two real engine configs, stores and admissions back one in-process Service
each. Public domain/ref selection must reach the matching Service; the default
domain never substitutes for an explicit target, and distill must use the
target engine's own capture lease.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tests") not in sys.path:
    sys.path.insert(0, str(ROOT / "tests"))
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import support  # noqa: E402
import identity  # noqa: E402
import mcp_server  # noqa: E402
from mindie_knowledge.loop.activation import Admission  # noqa: E402
from mindie_knowledge.loop.cli import rpc as shared_rpc  # noqa: E402
from mindie_knowledge.loop.engine import Engine  # noqa: E402
from mindie_knowledge.loop.store import Store  # noqa: E402
from mindie_knowledge.loop.transport import Service  # noqa: E402
from test_graded_feedback_entry import UNIT_ID, graded_unit  # noqa: E402


SESSION = "ses_domain_routing"
MISSING_SESSION = "ses_domain_routing_missing"
DEFAULT_DOMAIN = "operators"
TARGET_DOMAIN = "vllm-ascend"


class DomainRoutingEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kimi-domain-routing-")).resolve()
        with patch.object(support, "redactor_executable", return_value=sys.executable):
            self.config = support.make_config(self.tmp)
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        self.default_engine_path = Path(adapter["engine_config"])
        default_engine = json.loads(self.default_engine_path.read_text(encoding="utf-8"))

        default_engine["domain"] = DEFAULT_DOMAIN
        default_engine["root"] = str(self.tmp / "operators-root")
        default_engine["admission_path"] = str(
            self.tmp / "operators-root" / "admission.sqlite3")
        support.write_json(self.default_engine_path, default_engine)

        target_engine = dict(
            default_engine,
            domain=TARGET_DOMAIN,
            root=str(self.tmp / "vllm-root"),
            admission_path=str(self.tmp / "vllm-root" / "admission.sqlite3"),
        )
        self.target_engine_path = self.tmp / "vllm-ascend.engine.json"
        support.write_json(self.target_engine_path, target_engine)

        adapter["domain_configs"] = {TARGET_DOMAIN: str(self.target_engine_path)}
        support.write_json(self.config, adapter)
        self.adapter = adapter
        self.default_engine = default_engine
        self.target_engine = target_engine

        self.default_store = Store(default_engine["root"], default_engine["domain"])
        self.target_store = Store(target_engine["root"], target_engine["domain"])
        self.default_admission = Admission(default_engine["admission_path"])
        self.target_admission = Admission(target_engine["admission_path"])
        self.default_admission.activate(
            SESSION, project_root=str(self.tmp / "project-default"))
        self.target_admission.activate(
            SESSION, project_root=str(self.tmp / "project-target"))
        self.default_admission.activate(
            MISSING_SESSION, project_root=str(self.tmp / "project-default"))

        self.default_unit = graded_unit(
            1, "Operators default-domain rule for the routing fixture.\n")
        self.target_unit = graded_unit(
            7, "vllm-ascend target-domain rule for the routing fixture.\n")
        self.default_store.graded.install([self.default_unit])
        self.target_store.graded.install([self.target_unit])

        self.env_patch = patch.dict(os.environ, {
            "MINDIE_KIMI_CONFIG": str(self.config),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
        })
        self.env_patch.start()

        self.services = []
        self.threads = []
        for store, admission, engine_config in (
            (self.default_store, self.default_admission, default_engine),
            (self.target_store, self.target_admission, target_engine),
        ):
            engine = Engine(
                store,
                settings_path=engine_config.get("community_config"),
                admission=admission,
                redactor_executable=engine_config.get("redactor_executable"),
                summary_command=engine_config.get("summary_command"),
            )
            service = Service(engine, admission=admission)
            thread = threading.Thread(target=service.serve, daemon=True)
            thread.start()
            self.services.append(service)
            self.threads.append(thread)
        self.default_service, self.target_service = self.services
        self._wait_ready()

        connections = {
            self.default_engine_path.resolve(): self.default_service.connection,
            self.target_engine_path.resolve(): self.target_service.connection,
        }

        def selected_connection(engine_file=None):
            try:
                return connections[Path(engine_file).resolve()]
            except KeyError:
                raise AssertionError(f"unexpected engine config: {engine_file}") from None

        self.connection_patch = patch(
            "knowledge_service.existing_service", side_effect=selected_connection)
        self.connection_mock = self.connection_patch.start()
        self.ensure_patch = patch(
            "knowledge_service.ensure_service",
            side_effect=AssertionError("unexpected service start"),
        )
        self.ensure_patch.start()

    def _wait_ready(self):
        for service, thread in zip(self.services, self.threads):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    shared_rpc(service.connection, "status", timeout=0.5)
                    break
                except Exception:
                    if not thread.is_alive():
                        self.fail("in-process knowledge service exited during startup")
                    time.sleep(0.02)
            else:
                self.fail("in-process knowledge service did not become ready")

    def tearDown(self):
        try:
            self.ensure_patch.stop()
        finally:
            try:
                self.connection_patch.stop()
            finally:
                for service in self.services:
                    service.http.shutdown()
                    service.http.server_close()
                for thread in self.threads:
                    thread.join(timeout=5)
                try:
                    self.target_store.close()
                finally:
                    try:
                        self.default_store.close()
                    finally:
                        self.env_patch.stop()
                        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, nonce, name, arguments, session=SESSION):
        args = dict(arguments, request_nonce=nonce)
        identity.publish_nonce_bind(
            session, nonce,
            f"mcp__plugin_mindie-agent_knowledge__{name}",
            f"tool_{nonce}", args,
        )
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": nonce,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        })
        return response["result"]

    def _query(self, nonce, domain=None):
        arguments = {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
        }
        if domain is not None:
            arguments["domain"] = domain
        result = self._call(nonce, "knowledge_query", arguments)
        self.assertFalse(result["isError"], result)
        return result["structuredContent"]["results"][0]

    def _votes(self, store):
        return [dict(row) for row in store.db.execute("SELECT * FROM votes")]

    def test_explicit_query_domain_selects_that_engines_store(self):
        default_hit = self._query("nonce-domain-default")
        target_hit = self._query("nonce-domain-target", TARGET_DOMAIN)
        explicit_default = self._query("nonce-domain-explicit-default", DEFAULT_DOMAIN)

        self.assertEqual(
            default_hit["feedback_ref"],
            f"mindie://{DEFAULT_DOMAIN}/units/{UNIT_ID}@{self.default_unit.revision}",
        )
        self.assertEqual(default_hit["revision"], 1)
        self.assertEqual(
            target_hit["feedback_ref"],
            f"mindie://{TARGET_DOMAIN}/units/{UNIT_ID}@{self.target_unit.revision}",
        )
        self.assertEqual(target_hit["revision"], 7)
        self.assertEqual(explicit_default["feedback_ref"], default_hit["feedback_ref"])

    def test_native_generation_routes_override_the_old_bootstrap_map(self):
        generation = self.tmp / 'generation.adapter.json'
        selected = dict(self.adapter, profile_config=str(self.config))
        support.write_json(generation, selected)
        stale = dict(self.adapter, domain_configs={TARGET_DOMAIN: str(self.tmp / 'stale.engine.json')})
        support.write_json(self.config, stale)
        with patch.dict(os.environ, {'MINDIE_KIMI_CONFIG': str(generation)}):
            hit = self._query('nonce-current-generation-target', TARGET_DOMAIN)
        self.assertEqual(hit['revision'], 7)

    def test_uri_explain_and_feedback_use_the_reference_domain(self):
        target_hit = self._query("nonce-domain-target-ref", TARGET_DOMAIN)
        ref = target_hit["feedback_ref"]

        explained = self._call("nonce-domain-explain", "knowledge_explain", {"ref": ref})
        self.assertFalse(explained["isError"], explained)
        body = explained["structuredContent"]
        self.assertEqual(body["domain"], TARGET_DOMAIN)
        self.assertEqual(body["id"], UNIT_ID)
        self.assertEqual(body["revision"], 7)
        self.assertIn("vllm-ascend target-domain rule", body["content"])

        voted = self._call("nonce-domain-feedback", "knowledge_feedback", {
            "ref": ref,
            "rating": "up",
            "reason": "Target-domain fixture confirmation.",
        })
        self.assertFalse(voted["isError"], voted)
        vote = voted["structuredContent"]
        self.assertEqual(vote["unit_id"], UNIT_ID)
        self.assertEqual(vote["revision"], 7)
        self.assertIs(vote["publishable"], False)

        target_rows = self._votes(self.target_store)
        self.assertEqual(len(target_rows), 1)
        self.assertEqual(target_rows[0]["entry_id"], UNIT_ID)
        self.assertEqual(target_rows[0]["revision"], str(self.target_unit.revision))
        self.assertEqual(self._votes(self.default_store), [])

    def test_unknown_domain_and_declared_domain_mismatch_are_visible(self):
        before = self.connection_mock.call_count

        unknown = self._call("nonce-domain-unknown", "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "domain": "absent-domain",
        })
        self.assertTrue(unknown["isError"], unknown)
        self.assertIn("unknown domain", unknown["content"][0]["text"])

        wrong_engine = self.tmp / "wrong-domain.engine.json"
        support.write_json(wrong_engine, dict(self.target_engine, domain=DEFAULT_DOMAIN))
        adapter = dict(self.adapter, domain_configs={
            **self.adapter["domain_configs"],
            "wrong-domain": str(wrong_engine),
        })
        support.write_json(self.config, adapter)

        mismatch = self._call("nonce-domain-mismatch", "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "domain": "wrong-domain",
        })
        self.assertTrue(mismatch["isError"], mismatch)
        self.assertIn("declares", mismatch["content"][0]["text"])
        self.assertEqual(self.connection_mock.call_count, before)

    def test_explicit_domain_and_reference_conflict_is_visible(self):
        conflict_ref = f"mindie://{TARGET_DOMAIN}/units/{UNIT_ID}@{self.target_unit.revision}"
        with self.assertRaisesRegex(ValueError, "conflicts"):
            mcp_server._select_engine_path(domain=DEFAULT_DOMAIN, ref=conflict_ref)

        before = self.connection_mock.call_count
        result = self._call("nonce-domain-conflict", "knowledge_explain", {
            "ref": conflict_ref,
            "domain": DEFAULT_DOMAIN,
        })
        self.assertTrue(result["isError"], result)
        self.assertIn("conflicts", result["content"][0]["text"])
        self.assertEqual(self.connection_mock.call_count, before)

    def test_installed_runtime_without_router_keeps_default_and_rejects_explicit_domain(self):
        default_hit = self._query("nonce-old-runtime-default")
        with patch.dict(sys.modules, {"mindie_knowledge.loop.domain_routes": None}):
            explained = self._call("nonce-old-runtime-explain", "knowledge_explain", {
                "ref": default_hit["feedback_ref"],
            })
            self.assertFalse(explained["isError"], explained)
            self.assertEqual(explained["structuredContent"]["domain"], DEFAULT_DOMAIN)

            rejected = self._call("nonce-old-runtime-explicit", "knowledge_query", {
                "query": "gradedfeedbackprobe",
                "domain": TARGET_DOMAIN,
            })
        self.assertTrue(rejected["isError"], rejected)
        self.assertIn("routing is unavailable", rejected["content"][0]["text"])

    def test_missing_target_distill_lease_fails_before_service_or_model(self):
        before = self.connection_mock.call_count
        with patch("mindie_knowledge.loop.cli.distill_selected") as distill:
            failed = self._call("nonce-domain-missing-lease", "knowledge_distill", {
                "domain": TARGET_DOMAIN,
                "inputs": [{
                    "kind": "authorized_transcript",
                    "outcome": "completed",
                    "source_locator": "fixture@abcdef1:probe.txt",
                    "content": "Target-domain lease fixture.",
                    "evidence_refs": ["fixture-observation"],
                }],
                "task_state": "completed",
            }, session=MISSING_SESSION)
        self.assertTrue(failed["isError"], failed)
        text = failed["content"][0]["text"]
        self.assertIn("not bound", text)
        self.assertIn(TARGET_DOMAIN, text)
        distill.assert_not_called()
        self.assertEqual(self.connection_mock.call_count, before)

    def test_bound_native_task_can_read_target_without_capture_authority(self):
        self.assertIsNone(self.target_admission.active_lease(MISSING_SESSION))
        queried = self._call("nonce-public-target", "knowledge_query", {
            "query": "gradedfeedbackprobe", "domain": TARGET_DOMAIN,
            "include_unknown": True,
        }, session=MISSING_SESSION)
        self.assertFalse(queried["isError"], queried)
        hit = queried["structuredContent"]["results"][0]
        explained = self._call("nonce-public-explain", "knowledge_explain", {
            "ref": hit["feedback_ref"],
        }, session=MISSING_SESSION)
        self.assertFalse(explained["isError"], explained)
        self.assertEqual(explained["structuredContent"]["revision"], 7)
        rejected = self._call("nonce-public-feedback", "knowledge_feedback", {
            "ref": hit["feedback_ref"], "rating": "up", "reason": "read-only task",
        }, session=MISSING_SESSION)
        self.assertTrue(rejected["isError"], rejected)
        self.assertIsNone(self.target_admission.active_lease(MISSING_SESSION))
        self.assertEqual(self._votes(self.target_store), [])

    def test_distill_forwards_the_target_engines_own_lease(self):
        default_token = self.default_admission.active_lease(SESSION)["token"]
        target_token = self.target_admission.active_lease(SESSION)["token"]
        self.assertNotEqual(default_token, target_token)

        with patch("mindie_knowledge.loop.cli.distill_selected") as distill:
            distill.return_value = {"status": "noop", "model_calls": 0}
            result = self._call(SESSION, "knowledge_distill", {
                "domain": TARGET_DOMAIN,
                "inputs": [{
                    "kind": "authorized_transcript",
                    "outcome": "completed",
                    "source_locator": "fixture@abcdef1:probe.txt",
                    "content": "Target-domain lease fixture.",
                    "evidence_refs": ["fixture-observation"],
                }],
                "task_state": "completed",
            })
        self.assertFalse(result["isError"], result)
        distill.assert_called_once()
        engine_path, payload = distill.call_args.args
        self.assertEqual(Path(engine_path).resolve(), self.target_engine_path.resolve())
        self.assertEqual(payload["_session_id"], SESSION)
        self.assertEqual(payload["_activation"], target_token)
        self.assertNotEqual(payload["_activation"], default_token)


if __name__ == "__main__":
    unittest.main()
