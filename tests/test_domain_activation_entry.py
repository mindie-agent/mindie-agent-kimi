"""Native entry activation for an explicit additional knowledge domain.

The host's verified session/cwd drive the target Admission; the default-domain
lease is never replaced. Query, feedback and distill then resolve the target
lease through the existing routing path.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
import types
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


SESSION = "ses_domain_activation"
OTHER_SESSION = "ses_domain_activation_other"
DEFAULT_DOMAIN = "operators"
TARGET_DOMAIN = "vllm-ascend"
ENTRY_TOOL = "mcp__plugin_mindie-agent_knowledge__mindie_entry"


class DomainActivationEntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kimi-domain-activation-")).resolve()
        self.home = self.tmp / "kimi-home"
        with patch.object(support, "redactor_executable", return_value=sys.executable):
            self.config = support.make_config(self.tmp, sharing=True, roots=[self.tmp])
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        self.default_engine_path = Path(adapter["engine_config"])
        default_engine = json.loads(self.default_engine_path.read_text(encoding="utf-8"))
        default_engine["domain"] = DEFAULT_DOMAIN
        default_engine["root"] = str(self.tmp / "operators-root")
        default_engine["admission_path"] = str(
            self.tmp / "operators-root" / "admission.sqlite3")
        support.write_json(self.default_engine_path, default_engine)

        self.target_community = self.tmp / "vllm.community.json"
        self.target_consent = self.tmp / "vllm-consent.json"
        support.write_json(self.target_consent, {
            "schema": "mindie-consent/1", "choice": "contribute", "choice_at": 1.0,
        })
        support.write_json(self.target_community, {
            "schema": "mindie-community-config/1",
            "enabled": True,
            "generation": "gen-target",
            "enabled_at": 1.0,
            "repository": "owner/repo",
            "branch": "main",
            "project_roots": [str(self.tmp)],
            "idle_seconds": 300,
            "visibility": "public",
            "consent_config": str(self.target_consent),
        })
        target_engine = dict(
            default_engine,
            domain=TARGET_DOMAIN,
            root=str(self.tmp / "vllm-root"),
            admission_path=str(self.tmp / "vllm-root" / "admission.sqlite3"),
            community_config=str(self.target_community),
        )
        self.target_engine_path = self.tmp / "vllm-ascend.engine.json"
        support.write_json(self.target_engine_path, target_engine)

        adapter["domain_configs"] = {TARGET_DOMAIN: str(self.target_engine_path)}
        support.write_json(self.config, adapter)
        self.default_engine = default_engine
        self.target_engine = target_engine

        self.default_store = Store(default_engine["root"], default_engine["domain"])
        self.target_store = Store(target_engine["root"], target_engine["domain"])
        self.default_admission = Admission(default_engine["admission_path"])
        self.target_admission = Admission(target_engine["admission_path"])
        self.assertNotEqual(self.default_admission.path, self.target_admission.path)
        self.default_unit = graded_unit(
            1, "Operators default-domain activation fixture.\n")
        self.target_unit = graded_unit(
            7, "vllm-ascend target-domain activation fixture.\n")
        self.default_store.graded.install([self.default_unit])
        self.target_store.graded.install([self.target_unit])

        self.env_patch = patch.dict(os.environ, {
            "MINDIE_KIMI_CONFIG": str(self.config),
            "KIMI_CODE_HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
            "MINDIE_DIAGNOSTICS_ROOT": str(self.tmp / "diagnostics"),
        })
        self.env_patch.start()
        self.ensure_patch = patch("knowledge_service.ensure_service", return_value=None)
        self.ensure_patch.start()
        self.services = []
        self.threads = []
        self.connection_patch = None

    def tearDown(self):
        try:
            if self.connection_patch is not None:
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
                    try:
                        self.ensure_patch.stop()
                    finally:
                        self.env_patch.stop()
                        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_turn(self, session, activation, command="init"):
        support.write_session(
            self.home,
            session,
            support.turn_records(
                support.plugin_origin(command, activation, ""), command),
            cwd=self.tmp,
        )

    def _entry(self, session, activation, extra=None):
        self._write_turn(session, activation)
        nonce = f"nonce-{activation}"
        arguments = dict(op="init", request_nonce=nonce, **(extra or {}))
        identity.publish_nonce_bind(
            session, nonce, ENTRY_TOOL, f"tool-{activation}", arguments)
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": nonce,
            "method": "tools/call",
            "params": {"name": "mindie_entry", "arguments": arguments},
        })
        return response["result"]

    def _deactivate(self, session, activation):
        self._write_turn(session, activation, command="deactivate")
        nonce = f"nonce-{activation}"
        arguments = dict(op="deactivate", request_nonce=nonce)
        identity.publish_nonce_bind(
            session, nonce, ENTRY_TOOL, f"tool-{activation}", arguments)
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": nonce,
            "method": "tools/call",
            "params": {"name": "mindie_entry", "arguments": arguments},
        })
        return response["result"]

    def _call(self, session, nonce, name, arguments):
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

    def _start_services(self):
        for store, admission, engine_config in (
            (self.default_store, self.default_admission, self.default_engine),
            (self.target_store, self.target_admission, self.target_engine),
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
        connections = {
            self.default_engine_path.resolve(): self.services[0].connection,
            self.target_engine_path.resolve(): self.services[1].connection,
        }

        def selected(engine_file=None):
            try:
                return connections[Path(engine_file).resolve()]
            except KeyError:
                raise AssertionError(f"unexpected engine config: {engine_file}") from None

        self.connection_patch = patch(
            "knowledge_service.existing_service", side_effect=selected)
        self.connection_patch.start()
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

    def _target_community(self, *, enabled=True, roots=None):
        support.write_json(self.target_community, {
            "schema": "mindie-community-config/1",
            "enabled": enabled,
            "generation": "gen-target",
            "enabled_at": 1.0,
            "repository": "owner/repo",
            "branch": "main",
            "project_roots": [str(Path(item).resolve()) for item in (roots or [self.tmp])],
            "idle_seconds": 300,
            "visibility": "public",
            "consent_config": str(self.target_consent),
        })

    def _target_consent(self, choice="contribute"):
        support.write_json(self.target_consent, {
            "schema": "mindie-consent/1", "choice": choice, "choice_at": 1.0,
        })

    def test_target_activation_preserves_default_token_and_routes_target_calls(self):
        default = self._entry(SESSION, "act-default")
        self.assertFalse(default["isError"], default)
        default_before = self.default_admission.active_lease(SESSION)
        self.assertIsNotNone(default_before)
        self.assertIsNone(self.target_admission.active_lease(SESSION))

        activated = self._entry(SESSION, "act-target", {"domain": TARGET_DOMAIN})
        self.assertFalse(activated["isError"], activated)
        binding = activated["structuredContent"]["binding"]
        self.assertEqual(binding["domain"], TARGET_DOMAIN)
        self.assertEqual(binding["session"], SESSION)
        self.assertEqual(binding["project_root"], str(self.tmp))

        default_after = self.default_admission.active_lease(SESSION)
        target = self.target_admission.active_lease(SESSION)
        self.assertEqual(default_after["token"], default_before["token"])
        self.assertIsNotNone(target)
        self.assertNotEqual(target["token"], default_before["token"])
        self.assertEqual(target["project_root"], str(self.tmp))

        self._start_services()
        default_hit = self._call(SESSION, "nonce-default-query", "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
        })
        self.assertFalse(default_hit["isError"], default_hit)
        self.assertEqual(
            default_hit["structuredContent"]["results"][0]["feedback_ref"],
            f"mindie://{DEFAULT_DOMAIN}/units/{UNIT_ID}@{self.default_unit.revision}",
        )

        target_hit = self._call(SESSION, "nonce-target-query", "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
            "domain": TARGET_DOMAIN,
        })
        self.assertFalse(target_hit["isError"], target_hit)
        hit = target_hit["structuredContent"]["results"][0]
        self.assertEqual(
            hit["feedback_ref"],
            f"mindie://{TARGET_DOMAIN}/units/{UNIT_ID}@{self.target_unit.revision}",
        )
        self.assertEqual(hit["revision"], 7)

        voted = self._call(SESSION, "nonce-target-feedback", "knowledge_feedback", {
            "ref": hit["feedback_ref"],
            "rating": "up",
            "reason": "Target-domain activation fixture.",
        })
        self.assertFalse(voted["isError"], voted)
        self.assertIs(voted["structuredContent"]["publishable"], True)
        target_votes = [dict(row) for row in self.target_store.db.execute(
            "SELECT * FROM votes")]
        self.assertEqual(len(target_votes), 1)
        self.assertEqual(target_votes[0]["entry_id"], UNIT_ID)
        self.assertEqual(target_votes[0]["revision"], str(self.target_unit.revision))
        self.assertEqual(
            [dict(row) for row in self.default_store.db.execute("SELECT * FROM votes")],
            [],
        )

        with patch("mindie_knowledge.loop.cli.distill_selected") as distill:
            distill.return_value = {"status": "noop", "model_calls": 0}
            distilled = self._call(SESSION, "nonce-target-distill", "knowledge_distill", {
                "domain": TARGET_DOMAIN,
                "inputs": [{
                    "kind": "authorized_transcript",
                    "outcome": "completed",
                    "source_locator": "fixture@abcdef1:probe.txt",
                    "content": "Target-domain activation fixture.",
                    "evidence_refs": ["fixture-observation"],
                }],
                "task_state": "completed",
            })
        self.assertFalse(distilled["isError"], distilled)
        distill.assert_called_once()
        engine_path, payload = distill.call_args.args
        self.assertEqual(Path(engine_path).resolve(), self.target_engine_path.resolve())
        self.assertEqual(payload["_session_id"], SESSION)
        self.assertEqual(payload["_activation"], target["token"])
        self.assertNotEqual(payload["_activation"], default_after["token"])

    def test_target_disabled_missing_consent_or_out_of_scope_creates_no_lease(self):
        cases = (
            ("disabled", False, [self.tmp], "contribute", "not authorized"),
            ("no-consent", True, [self.tmp], None, "consent"),
            ("out-of-scope", True, [self.tmp / "elsewhere"], "contribute", "scope"),
        )
        for name, enabled, roots, consent, needle in cases:
            session = f"ses_activation_{name}"
            self._target_community(enabled=enabled, roots=roots)
            if consent is None:
                self.target_consent.unlink(missing_ok=True)
            else:
                self._target_consent(consent)
            with self.subTest(name=name):
                result = self._entry(session, f"act-{name}", {"domain": TARGET_DOMAIN})
                self.assertTrue(result["isError"], result)
                self.assertIn(needle, result["content"][0]["text"].lower())
                self.assertIsNone(self.target_admission.active_lease(session))
                self.assertIsNone(self.default_admission.active_lease(session))

    def test_target_query_without_binding_never_implicitly_activates(self):
        session = "ses_activation_unbound"
        result = self._call(session, "nonce-unbound-target", "knowledge_query", {
            "query": "gradedfeedbackprobe",
            "domain": TARGET_DOMAIN,
        })
        self.assertTrue(result["isError"], result)
        self.assertIn("not bound", result["content"][0]["text"])
        self.assertIsNone(self.target_admission.active_lease(session))
        self.assertIsNone(self.default_admission.active_lease(session))

    def test_domain_value_is_nonce_bound_and_caller_identity_is_rejected(self):
        session = "ses_activation_binding"
        self._write_turn(session, "act-bound")
        bound = dict(op="init", domain=TARGET_DOMAIN, request_nonce="nonce-bind-target")
        identity.publish_nonce_bind(
            session, "nonce-bind-target", ENTRY_TOOL, "tool-bind-target", bound)
        tampered = dict(bound, domain=DEFAULT_DOMAIN)
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": "bind-target",
            "method": "tools/call",
            "params": {"name": "mindie_entry", "arguments": tampered},
        })["result"]
        self.assertTrue(response["isError"], response)
        self.assertIn("does not match", response["content"][0]["text"])
        self.assertIsNone(self.target_admission.active_lease(session))

        escalation = {
            "op": "init", "domain": TARGET_DOMAIN,
            "request_nonce": "nonce-id-escalation", "_session_id": "other-session",
        }
        identity.publish_nonce_bind(
            session, "nonce-id-escalation", ENTRY_TOOL, "tool-id-escalation", escalation)
        response = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": "id-escalation",
            "method": "tools/call",
            "params": {"name": "mindie_entry", "arguments": escalation},
        })["result"]
        self.assertTrue(response["isError"], response)
        self.assertIn("native session identity", response["content"][0]["text"])

        session = "ses_activation_cwd"
        result = self._entry(session, "act-cwd", {
            "domain": TARGET_DOMAIN,
            "project_root": str(self.tmp / "caller-supplied-root"),
        })
        self.assertFalse(result["isError"], result)
        lease = self.target_admission.active_lease(session)
        self.assertIsNotNone(lease)
        self.assertEqual(lease["project_root"], str(self.tmp))
        self.assertIsNone(self.default_admission.active_lease(session))

    def test_native_deactivate_revokes_default_and_registered_target_only(self):
        for activation, extra in (
            ("act-default", None),
            ("act-target", {"domain": TARGET_DOMAIN}),
        ):
            result = self._entry(SESSION, activation, extra)
            self.assertFalse(result["isError"], result)
        self.default_admission.activate(
            OTHER_SESSION, project_root=str(self.tmp / "other-project"))
        self.target_admission.activate(
            OTHER_SESSION, project_root=str(self.tmp / "other-project"))
        other_default = self.default_admission.active_lease(OTHER_SESSION)
        other_target = self.target_admission.active_lease(OTHER_SESSION)
        current_default = self.default_admission.active_lease(SESSION)
        current_target = self.target_admission.active_lease(SESSION)
        self.assertIsNotNone(current_default)
        self.assertIsNotNone(current_target)

        # Revocation never consults capture consent.
        self._target_community(enabled=False)

        result = self._deactivate(SESSION, "act-deactivate")
        self.assertFalse(result["isError"], result)
        payload = result["structuredContent"]
        self.assertEqual(payload["status"], "inactive")
        self.assertIs(payload["deactivated"], True)
        self.assertNotIn("partial", payload)
        outcomes = {item["domain"]: item for item in payload["domains"]}
        self.assertEqual(outcomes[DEFAULT_DOMAIN]["status"], "inactive")
        self.assertEqual(outcomes[TARGET_DOMAIN]["status"], "inactive")
        self.assertTrue(all("engine_config" not in item for item in payload["domains"]))
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(current_default["token"], serialized)
        self.assertNotIn(current_target["token"], serialized)

        self.assertIsNone(self.default_admission.active_lease(SESSION))
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        self.assertEqual(
            self.default_admission.active_lease(OTHER_SESSION)["token"],
            other_default["token"],
        )
        self.assertEqual(
            self.target_admission.active_lease(OTHER_SESSION)["token"],
            other_target["token"],
        )

        # The native deactivate origin was consumed in the default admission.
        with sqlite3.connect(self.default_admission.path) as db:
            attempt = db.execute(
                "SELECT 1 FROM attempts WHERE session=? AND kind=? AND identity=?",
                (SESSION, "plugin_command", "act-deactivate"),
            ).fetchone()
        self.assertIsNotNone(attempt)

        replay_arguments = {
            "op": "deactivate",
            "request_nonce": "nonce-act-deactivate",
        }
        replay = mcp_server.handle("knowledge", {
            "jsonrpc": "2.0",
            "id": "deactivate-replay",
            "method": "tools/call",
            "params": {"name": "mindie_entry", "arguments": replay_arguments},
        })["result"]
        self.assertTrue(replay["isError"], replay)
        self.assertIn("already", replay["content"][0]["text"].lower())

    def test_native_deactivate_recovers_a_previously_default_only_revocation(self):
        for activation, extra in (
            ("act-default", None),
            ("act-target", {"domain": TARGET_DOMAIN}),
        ):
            result = self._entry(SESSION, activation, extra)
            self.assertFalse(result["isError"], result)
        self.default_admission.activate(
            OTHER_SESSION, project_root=str(self.tmp / "other-project"))
        self.target_admission.activate(
            OTHER_SESSION, project_root=str(self.tmp / "other-project"))
        self.assertTrue(self.default_admission.deactivate(SESSION))
        self.assertIsNone(self.default_admission.active_lease(SESSION))
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))

        result = self._deactivate(SESSION, "act-recover")
        self.assertFalse(result["isError"], result)
        payload = result["structuredContent"]
        self.assertEqual(payload["status"], "inactive")
        self.assertIs(payload["deactivated"], True)
        self.assertIsNone(self.default_admission.active_lease(SESSION))
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        self.assertIsNotNone(self.default_admission.active_lease(OTHER_SESSION))
        self.assertIsNotNone(self.target_admission.active_lease(OTHER_SESSION))

    def test_deactivate_without_domain_configs_keeps_old_core_path(self):
        result = self._entry(SESSION, "act-default")
        self.assertFalse(result["isError"], result)
        adapter = json.loads(self.config.read_text(encoding="utf-8"))
        adapter.pop("domain_configs", None)
        support.write_json(self.config, adapter)

        with patch.dict(sys.modules, {"mindie_knowledge.loop.domain_routes": None}):
            deactivated = self._deactivate(SESSION, "act-old-core")
        self.assertFalse(deactivated["isError"], deactivated)
        self.assertIs(deactivated["structuredContent"]["deactivated"], True)
        self.assertIsNone(self.default_admission.active_lease(SESSION))

    def test_partial_deactivate_keeps_successful_effect_visible_and_reports_error(self):
        self._entry(SESSION, "act-default")
        self._entry(SESSION, "act-target", {"domain": TARGET_DOMAIN})
        self.target_engine_path.unlink()

        result = self._deactivate(SESSION, "act-partial")
        self.assertTrue(result["isError"], result)
        payload = result["structuredContent"]
        self.assertEqual(payload["status"], "partial")
        self.assertFalse(payload["deactivated"])
        self.assertTrue(payload["changed"])
        self.assertIsNone(self.default_admission.active_lease(SESSION))
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))
        outcomes = {item["domain"]: item for item in payload["domains"]}
        self.assertEqual(outcomes[DEFAULT_DOMAIN]["status"], "inactive")
        self.assertEqual(outcomes[TARGET_DOMAIN]["status"], "unavailable")

    def test_registered_deactivate_requires_the_exact_optional_module(self):
        self._entry(SESSION, "act-default")
        self._entry(SESSION, "act-target", {"domain": TARGET_DOMAIN})
        with patch.dict(sys.modules, {"mindie_knowledge.loop.domain_routes": None}):
            missing = self._deactivate(SESSION, "act-missing-helper")
        self.assertTrue(missing["isError"], missing)
        self.assertIn("deactivation is unavailable", missing["content"][0]["text"])
        self.assertIsNotNone(self.default_admission.active_lease(SESSION))
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))

        fake = types.ModuleType("mindie_knowledge.loop.domain_routes")

        def broken(*_args, **_kwargs):
            raise ModuleNotFoundError(
                "No module named 'internal_dependency'", name="internal_dependency")

        fake.deactivate_registered_domains = broken
        with patch.dict(sys.modules, {"mindie_knowledge.loop.domain_routes": fake}):
            internal = self._deactivate(SESSION, "act-internal-error")
        self.assertTrue(internal["isError"], internal)
        self.assertIn("internal_dependency", internal["content"][0]["text"])
        self.assertIsNotNone(self.default_admission.active_lease(SESSION))
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))


if __name__ == "__main__":
    unittest.main()
