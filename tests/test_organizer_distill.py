import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "tests", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from support import SCRIPTS, env_for

import organizer
import organizer_distill
from mindie_knowledge.graded import distill
from mindie_knowledge.materials import summarizer


SOURCE_TOML = """default_model = "kimi-code/k3"
builtin_product_skills = false

[providers."managed:kimi-code"]
type = "kimi"
api_key = ""

[models."kimi-code/k3"]
provider = "managed:kimi-code"
model = "k3"
capabilities = [ "thinking", "tool_use" ]
"""
SESSION_DIGEST = "a" * 64
LOCATOR = "fixture@abcdef1:probe.txt"
EVIDENCE_REF = "fixture-observation"
CONTENT = "Fixture observation without secrets."
UNIT_ID = "unit-fixture-distill"


def _knowledge_path():
    return str(Path(__import__("mindie_knowledge").__file__).resolve().parents[1])


def _pythonpath():
    values = [_knowledge_path()]
    for item in os.environ.get("PYTHONPATH", "").split(os.pathsep):
        if item and item not in values:
            values.append(item)
    return os.pathsep.join(values)


def _frontmatter(source):
    return {
        "schema": "mindie-graded-unit/1",
        "id": UNIT_ID,
        "tier": "L3",
        "category": "workflow",
        "evidence_strength": "reproduced_empirical",
        "domain_path": "ops/probes",
        "inherits_from": [],
        "title": "Verified fixture workflow",
        "triggers": {"error_signatures": [], "keywords": ["fixture"]},
        "conditions": {"soc": ["Ascend910B"]},
        "provenance": {
            "source_type": "session_distill",
            "source_locator": source["source_locator"],
            "source_digest": source["source_digest"],
            "evidence_refs": [EVIDENCE_REF],
            "supersedes": [],
        },
    }


def _decision(request):
    source = request["sanitized_inputs"][0]
    frontmatter = _frontmatter(source)
    return {
        "schema_version": "mindie-distill-decision/1",
        "session_digest": request["session_digest"],
        "evidence_inputs": request["evidence_inputs"],
        "action": "propose_graded_units",
        "drop_reason": None,
        "units": [{
            "target_tier": frontmatter["tier"],
            "category": frontmatter["category"],
            "evidence_strength": frontmatter["evidence_strength"],
            "domain_path": frontmatter["domain_path"],
            "operation": "create",
            "target_unit_id": frontmatter["id"],
            "frontmatter": frontmatter,
            "markdown_body": "Apply the verified fixture workflow under the stated conditions.\n",
        }],
    }


NATIVE_STUB = r'''
import json
import sys

args = sys.argv[1:]
agent = args[args.index("--agent-file") + 1]
text = open(agent, encoding="utf-8").read()
envelope = None
for line in text.splitlines():
    if line.startswith("{") and '"output_schema"' in line:
        envelope = json.loads(line)
        break
if envelope is None:
    raise SystemExit(2)
request = envelope["request"]
source = request["sanitized_inputs"][0]
frontmatter = {
    "schema": "mindie-graded-unit/1",
    "id": "unit-fixture-distill",
    "tier": "L3",
    "category": "workflow",
    "evidence_strength": "reproduced_empirical",
    "domain_path": "ops/probes",
    "inherits_from": [],
    "title": "Verified fixture workflow",
    "triggers": {"error_signatures": [], "keywords": ["fixture"]},
    "conditions": {"soc": ["Ascend910B"]},
    "provenance": {
        "source_type": "session_distill",
        "source_locator": source["source_locator"],
        "source_digest": source["source_digest"],
        "evidence_refs": ["fixture-observation"],
        "supersedes": [],
    },
}
decision = {
    "schema_version": "mindie-distill-decision/1",
    "session_digest": request["session_digest"],
    "evidence_inputs": request["evidence_inputs"],
    "action": "propose_graded_units",
    "drop_reason": None,
    "units": [{
        "target_tier": "L3",
        "category": "workflow",
        "evidence_strength": "reproduced_empirical",
        "domain_path": "ops/probes",
        "operation": "create",
        "target_unit_id": "unit-fixture-distill",
        "frontmatter": frontmatter,
        "markdown_body": "Apply the verified fixture workflow under the stated conditions.\n",
    }],
}
sys.stdout.write(json.dumps(decision))
'''


class DistillOrganizerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / "config.toml").write_text(SOURCE_TOML)

    def tearDown(self):
        self._tmp.cleanup()

    def _env(self, **extra):
        values = {
            "KIMI_CODE_HOME": str(self.home),
            "MINDIE_KIMI_BIN": "/no/such/kimi",
            "PYTHONPATH": _pythonpath(),
        }
        values.update(extra)
        return patch.dict(os.environ, values)

    def _base_request(self, *, task_state="verified_milestone", outcome="verified", repetitions=2):
        selected = [{
            "kind": "authorized_transcript",
            "outcome": outcome,
            "source_locator": LOCATOR,
            "content": CONTENT,
            "source_digest": hashlib.sha256(CONTENT.encode()).hexdigest(),
            "evidence_refs": [EVIDENCE_REF],
            "repetitions": repetitions,
        }]
        request = {
            "schema_version": distill.REQUEST_SCHEMA,
            "session_digest": SESSION_DIGEST,
            "task_state": task_state,
            "evidence_inputs": sorted({item["kind"] for item in selected}),
            "sanitized_inputs": selected,
            "redaction_rules": [],
            "redaction_completed": True,
            "model_calls": 0,
        }
        core = {key: request[key] for key in distill.BASE_REQUEST_KEYS if key != "request_digest"}
        request["request_digest"] = hashlib.sha256(distill.canonical(core).encode()).hexdigest()
        return request

    def _bound(self, request):
        return distill.with_worker_identity(request, organizer.identity())

    def _native_stub(self):
        script = self.root / "native_stub.py"
        script.write_text(NATIVE_STUB)
        wrapper = self.root / "kimi-stub"
        wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
            + shlex.quote(str(script)) + ' "$@"\n'
        )
        wrapper.chmod(0o755)
        return str(wrapper)

    def test_original_identity_declares_c6_and_binds_request(self):
        with self._env():
            identity = organizer.identity()
            summarizer.validate_identity(identity)
            self.assertIn(distill.REQUEST_SCHEMA, identity["capabilities"])
            request = self._bound(self._base_request())
            self.assertEqual(distill.validate_request(request), request)

    @unittest.skipUnless(os.name == "posix", "native stub needs a shell")
    def test_native_protocol_returns_standard_summary_outcome(self):
        with self._env(MINDIE_KIMI_BIN=self._native_stub()):
            request = self._bound(self._base_request())
            outcome = organizer.run(request)
            expected = _decision(request)
        self.assertEqual(outcome["schema"], summarizer.OUTCOME_SCHEMA)
        self.assertEqual(outcome["status"], "returned")
        self.assertEqual(outcome["result"], expected)
        self.assertEqual(outcome["model_calls"], 1)
        self.assertIsNone(outcome["error"])
        self.assertEqual(outcome["billing_status"], "unknown")
        self.assertEqual(summarizer.validate_outcome(outcome, request)["status"], "returned")

    def test_pre_send_failure_uses_standard_failed_outcome(self):
        with self._env():
            request = self._bound(self._base_request())
            outcome = organizer.run(request)
        self.assertEqual(outcome["schema"], summarizer.OUTCOME_SCHEMA)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["model_calls"], 0)
        self.assertEqual(outcome["error"], "configuration")
        self.assertEqual(outcome["error_reason"], "native_start_failed")
        self.assertIsNone(outcome["result"])
        self.assertEqual(outcome["billing_status"], "not_called")
        summarizer.validate_outcome(outcome, request)

    @unittest.skipUnless(os.name == "posix", "native stub needs a shell")
    def test_post_send_failure_is_uncertain_and_not_retried(self):
        calls = self.root / "calls"
        stub = self.root / "failing-kimi"
        stub.write_text('#!/bin/sh\necho x >> "$MINDIE_TEST_CALLS"\nexit 1\n')
        stub.chmod(0o755)
        with self._env(MINDIE_KIMI_BIN=str(stub), MINDIE_TEST_CALLS=str(calls)):
            request = self._bound(self._base_request())
            outcome = organizer.run(request)
        self.assertEqual(outcome["schema"], summarizer.OUTCOME_SCHEMA)
        self.assertEqual(outcome["status"], "outcome_unknown")
        self.assertEqual(outcome["model_calls"], 1)
        self.assertEqual(outcome["error"], "native")
        self.assertEqual(outcome["error_reason"], "native_exit_nonzero")
        self.assertIsNone(outcome["result"])
        self.assertEqual(outcome["billing_status"], "unknown")
        self.assertEqual(calls.read_text().count("x"), 1)
        summarizer.validate_outcome(outcome, request)

    def test_usage_is_preserved_in_the_standard_outcome(self):
        with self._env():
            request = self._bound(self._base_request())
            expected = _decision(request)

            def fake_invoke(prompt, receipt, **kwargs):
                receipt.update(
                    native_started=True, turn_completed=True,
                    final_message=json.dumps(expected),
                    usage={"input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 8},
                )

            with patch.object(organizer, "_invoke_native", fake_invoke):
                outcome = organizer.run(request)
        self.assertEqual(outcome["status"], "returned")
        self.assertEqual(outcome["result"], expected)
        self.assertEqual(outcome["usage"], {
            "input_tokens": 12, "cached_input_tokens": 3, "output_tokens": 8,
        })
        self.assertTrue(outcome["usage_known"])
        self.assertEqual(outcome["billing_status"], "reported")
        summarizer.validate_outcome(outcome, request)

    def test_in_flight_request_returns_standard_noop_without_a_model_call(self):
        with self._env():
            request = self._bound(self._base_request(task_state="in_flight"))
            with patch.object(organizer, "_invoke_native", side_effect=AssertionError("paid call")):
                outcome = organizer.run(request)
        self.assertEqual(outcome["status"], "returned")
        self.assertEqual(outcome["model_calls"], 0)
        self.assertFalse(outcome["usage_known"])
        self.assertEqual(outcome["billing_status"], "not_called")
        self.assertEqual(outcome["result"]["action"], "noop")
        self.assertEqual(outcome["result"]["drop_reason"], "in_flight_incomplete")
        summarizer.validate_outcome(outcome, request)

    def test_completed_or_single_observation_milestone_uses_shared_gate(self):
        for task_state, evidence in (("verified_milestone", "observed"), ("completed", "verified")):
            with self.subTest(task_state=task_state, evidence=evidence):
                with self._env():
                    request = self._bound(self._base_request(
                        task_state=task_state, outcome=evidence,
                    ))
                    expected = _decision(request)
                    if evidence == "observed":
                        proposed = expected["units"][0]
                        proposed.update(target_tier="L2", category="sop", evidence_strength="single_observation")
                        proposed["frontmatter"].update(tier="L2", category="sop", evidence_strength="single_observation")

                    def complete_native(prompt, receipt, **kwargs):
                        receipt.update(native_started=True, turn_completed=True, final_message=json.dumps(expected))

                    with patch.object(organizer, "_invoke_native", complete_native):
                        outcome = organizer.run(request)
                self.assertEqual(outcome["status"], "returned")
                self.assertEqual(outcome["model_calls"], 1)
                self.assertEqual(outcome["result"], expected)
                summarizer.validate_outcome(outcome, request)

    def test_invalid_model_decision_is_rejected_by_shared_validation(self):
        with self._env():
            request = self._bound(self._base_request())
            invalid = _decision(request)
            invalid["session_digest"] = "b" * 64

            def fake_invoke(prompt, receipt, **kwargs):
                receipt.update(
                    native_started=True, turn_completed=True,
                    final_message=json.dumps(invalid),
                )

            with patch.object(organizer, "_invoke_native", fake_invoke):
                outcome = organizer.run(request)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["model_calls"], 1)
        self.assertEqual(outcome["error"], "invalid_result")
        self.assertEqual(outcome["error_reason"], "result_contract_invalid")
        self.assertIsNone(outcome["result"])
        summarizer.validate_outcome(outcome, request)

    def test_cleanup_bookkeeping_does_not_reverse_the_returned_decision(self):
        with self._env():
            request = self._bound(self._base_request())
            expected = _decision(request)

            def fake_invoke(prompt, receipt, **kwargs):
                receipt.update(
                    native_started=True, turn_completed=True,
                    final_message=json.dumps(expected), cleanup_failed=True,
                )

            with patch.object(organizer, "_invoke_native", fake_invoke):
                outcome = organizer.run(request)
        self.assertEqual(outcome["status"], "returned")
        self.assertEqual(outcome["result"], expected)
        self.assertTrue(outcome["cleanup_failed"])
        summarizer.validate_outcome(outcome, request)

    def test_supplied_identity_must_match_this_worker(self):
        with self._env():
            request = self._bound(self._base_request())
            request["policy_identity"]["model"] = "other-model"
            with patch.object(organizer, "_invoke_native", side_effect=AssertionError("paid call")):
                outcome = organizer.run(request)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["model_calls"], 0)
        self.assertEqual(outcome["error"], "configuration")
        self.assertEqual(outcome["error_reason"], "worker_policy_changed")
        summarizer.validate_outcome(outcome, request)

    def test_missing_shared_c6_api_fails_closed_without_a_model_call(self):
        with self._env():
            request = self._bound(self._base_request())
            with patch.object(
                organizer_distill, "_shared",
                side_effect=organizer_distill.UnsupportedDistillError("unavailable"),
            ):
                identity = organizer.identity()
                self.assertNotIn(distill.REQUEST_SCHEMA, identity.get("capabilities", []))
                outcome = organizer.run(request)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["model_calls"], 0)
        self.assertEqual(outcome["error"], "configuration")
        self.assertEqual(outcome["error_reason"], "dependency_unavailable")
        self.assertIsNone(outcome["result"])
        summarizer.validate_outcome(outcome, request)

    def test_cli_unknown_schema_is_explicit(self):
        env = env_for(kimi_home=self.home, extra={"PYTHONPATH": _pythonpath()})
        rejected = subprocess.run(
            [sys.executable, str(SCRIPTS / "organizer.py")],
            input=json.dumps({"schema_version": "mindie-distill-request/2"}),
            text=True, capture_output=True, timeout=30, env=env,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(rejected.stdout.strip(), "")
        self.assertIn("unsupported schema", rejected.stderr)


class SharedWorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        (self.home / "config.toml").write_text(SOURCE_TOML)
        self.stub = self._native_stub()
        self.scanner = self._scanner()

    def tearDown(self):
        self._tmp.cleanup()

    def _scanner(self):
        matches = list(
            (Path.home() / ".cache/mindie-agent/gitleaks/8.30.1").glob("*/gitleaks")
        )
        if not matches:
            self.skipTest("installed pinned scanner is not present; no download in component tests")
        return str(matches[0])

    def _native_stub(self):
        script = self.root / "native_stub.py"
        script.write_text(NATIVE_STUB)
        wrapper = self.root / "kimi-stub"
        wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " "
            + shlex.quote(str(script)) + ' "$@"\n'
        )
        wrapper.chmod(0o755)
        return str(wrapper)

    @unittest.skipUnless(os.name == "posix", "native stub needs a shell")
    def test_shared_worker_runs_the_real_organizer_subprocess(self):
        from mindie_knowledge.graded import worker as graded_worker
        from mindie_knowledge.loop.engine import Engine
        from mindie_knowledge.loop.store import Store

        inputs = [{
            "kind": "authorized_transcript",
            "outcome": "verified",
            "source_locator": LOCATOR,
            "content": CONTENT,
            "evidence_refs": [EVIDENCE_REF],
            "repetitions": 2,
        }]
        store = Store(self.root / "store", "vllm-ascend")
        try:
            engine = Engine(
                store,
                redactor_executable=self.scanner,
                summary_command=[sys.executable, str(SCRIPTS / "organizer.py")],
            )
            with patch.dict(os.environ, {
                "KIMI_CODE_HOME": str(self.home),
                "MINDIE_KIMI_BIN": self.stub,
                "PYTHONPATH": _pythonpath(),
            }):
                result = graded_worker.run(
                    engine, inputs, session_digest=SESSION_DIGEST,
                    task_state="verified_milestone",
                )
            self.assertEqual(result["status"], "applied")
            self.assertEqual(result["admitted_unit_ids"], [UNIT_ID])
            self.assertEqual(result["model_calls"], 1)
            self.assertEqual(result["publication"], "not_requested")
            found = store.query("fixture", conditions={"soc": "Ascend910B"})
            self.assertEqual([item["id"] for item in found["results"]], [UNIT_ID])
        finally:
            store.close()

    def test_shared_worker_in_flight_noop_never_starts_the_native_worker(self):
        from mindie_knowledge.graded import worker as graded_worker
        from mindie_knowledge.loop.engine import Engine
        from mindie_knowledge.loop.store import Store

        inputs = [{
            "kind": "authorized_transcript",
            "outcome": "verified",
            "source_locator": LOCATOR,
            "content": CONTENT,
            "evidence_refs": [EVIDENCE_REF],
            "repetitions": 2,
        }]
        store = Store(self.root / "noop-store", "vllm-ascend")
        try:
            engine = Engine(
                store,
                redactor_executable=self.scanner,
                summary_command=[sys.executable, "/no/such/organizer.py"],
            )
            result = graded_worker.run(
                engine, inputs, session_digest=SESSION_DIGEST, task_state="in_flight",
            )
            self.assertEqual(result["status"], "noop")
            self.assertEqual(result["publication"], "not_requested")
        finally:
            store.close()

    @unittest.skipUnless(os.name == "posix", "native stub needs a shell")
    def test_registered_mcp_entry_routes_through_real_service_and_engine(self):
        import support
        import admission
        import identity
        import mcp_server
        import service_handoff
        from mindie_knowledge.loop.cli import connection_path, rpc
        from mindie_knowledge.loop.locks import lock_held

        tmp = self.root / "service"
        tmp.mkdir()
        with patch.object(support, "redactor_executable", return_value=self.scanner):
            config = support.make_config(tmp, sharing=True, roots=[tmp])
        adapter = json.loads(config.read_text())
        community_path = Path(adapter["community_config"])
        community = json.loads(community_path.read_text())
        community["repository"] = None
        community_path.write_text(json.dumps(community) + "\n")
        engine_path = adapter["engine_config"]
        engine_data = json.loads(Path(engine_path).read_text())
        consumer = connection_path(engine_data).with_name("consumer.lock")
        with patch.dict(os.environ, {
            "MINDIE_KIMI_CONFIG": str(config),
            "KIMI_CODE_HOME": str(self.home),
            "MINDIE_KIMI_BIN": self.stub,
            "PYTHONPATH": _pythonpath(),
        }):
            lease = admission.activate("ses_e2e", project_root=str(tmp.resolve()))
            process = subprocess.Popen(
                [sys.executable, "-m", "mindie_knowledge.loop.cli", "serve",
                 "--config", engine_path],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                env=os.environ.copy(),
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        self.fail("service exited before readiness: "
                                  + process.stderr.read().decode("utf-8", "replace"))
                    if lock_held(consumer) is True:
                        try:
                            connection = service_handoff.connect(engine_data)
                            status = rpc(connection, "status", timeout=.3)
                            if status.get("admission_frozen") is False:
                                break
                        except (OSError, ValueError):
                            pass
                    time.sleep(.05)
                else:
                    self.fail("service readiness deadline")

                args = dict(
                    inputs=[{
                        "kind": "authorized_transcript",
                        "outcome": "verified",
                        "source_locator": LOCATOR,
                        "content": CONTENT,
                        "evidence_refs": [EVIDENCE_REF],
                        "repetitions": 2,
                    }],
                    task_state="verified_milestone",
                    request_nonce="nonce-e2e1",
                )
                identity.publish_nonce_bind(
                    "ses_e2e",
                    "nonce-e2e1",
                    "mcp__plugin-mindie-agent_knowledge__knowledge_distill",
                    "tool-e2e1",
                    args,
                )
                response = mcp_server.handle("knowledge", dict(
                    jsonrpc="2.0", id=40, method="tools/call",
                    params=dict(name="knowledge_distill", arguments=args),
                ))
                self.assertFalse(response["result"]["isError"], response)
                payload = response["result"]["structuredContent"]
                self.assertEqual(payload["status"], "applied")
                self.assertEqual(payload["admitted_unit_ids"], [UNIT_ID])
                self.assertEqual(payload["model_calls"], 1)
                # The live generation authorizes this admission for the existing
                # outbox; repository=None keeps this fixture from remote publication.
                self.assertEqual(payload["publication"], "eligible_for_existing_outbox")

                query_args = dict(
                    query="fixture",
                    conditions={"soc": "Ascend910B"},
                    domain_path="ops/probes",
                    token_budget=123,
                    include_unknown=True,
                    request_nonce="nonce-e2e2",
                )
                identity.publish_nonce_bind(
                    "ses_e2e",
                    "nonce-e2e2",
                    "mcp__plugin-mindie-agent_knowledge__knowledge_query",
                    "tool-e2e2",
                    query_args,
                )
                queried = mcp_server.handle("knowledge", dict(
                    jsonrpc="2.0", id=41, method="tools/call",
                    params=dict(name="knowledge_query", arguments=query_args),
                ))
                self.assertFalse(queried["result"]["isError"], queried)
                found = queried["result"]["structuredContent"]
                self.assertEqual([item["id"] for item in found["results"]], [UNIT_ID])
            finally:
                try:
                    service_handoff.stop(engine_path)
                    process.wait(timeout=5)
                except Exception:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=3)
                if process.stderr is not None:
                    process.stderr.close()


if __name__ == "__main__":
    unittest.main()
