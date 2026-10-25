import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SCRIPTS, env_for, make_config, plugin_origin, turn_records, write_session


class McpTests(unittest.TestCase):
    def config(self, tmp):
        import support

        with patch.object(support, "redactor_executable", return_value=sys.executable):
            return make_config(tmp)

    def rpc(self, proc, message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line)

    def start(self, surface, config, extra=None):
        proc = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "mcp_server.py"), surface],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            env=env_for(config, extra=extra),
            cwd=str(SCRIPTS.parent),
        )
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                self.addCleanup(stream.close)
        return proc

    def test_knowledge_list_requires_nonce_and_rejects_foreign_session(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            proc = self.start("knowledge", config)
            try:
                init = self.rpc(proc, dict(jsonrpc="2.0", id=1, method="initialize", params={}))
                self.assertEqual(init["result"]["serverInfo"]["name"], "mindie-kimi-knowledge")
                listed = self.rpc(proc, dict(jsonrpc="2.0", id=2, method="tools/list"))
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertEqual(
                    names,
                    {
                        "mindie_entry", "knowledge_query", "knowledge_explain",
                        "knowledge_feedback", "knowledge_distill",
                    },
                )
                missing = self.rpc(
                    proc,
                    dict(
                        jsonrpc="2.0",
                        id=3,
                        method="tools/call",
                        params=dict(
                            name="knowledge_query",
                            arguments=dict(query="npu", session_id="ses_model"),
                        ),
                    ),
                )
                self.assertTrue(missing["result"]["isError"])
            finally:
                proc.stdin.close()
                proc.kill()
                proc.wait(timeout=2)

    def test_bound_nonce_argument_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import identity

            args = dict(query="npu", request_nonce="nonce-bound1")
            identity.publish_nonce_bind(
                "ses_ok",
                "nonce-bound1",
                "mcp__plugin-mindie-agent_knowledge__knowledge_query",
                "tool_1",
                args,
            )
            proc = self.start("knowledge", config)
            try:
                self.rpc(proc, dict(jsonrpc="2.0", id=1, method="initialize", params={}))
                mismatch = self.rpc(
                    proc,
                    dict(
                        jsonrpc="2.0",
                        id=2,
                        method="tools/call",
                        params=dict(
                            name="knowledge_query",
                            arguments=dict(query="other", request_nonce="nonce-bound1"),
                        ),
                    ),
                )
                self.assertTrue(mismatch["result"]["isError"])
            finally:
                proc.stdin.close()
                proc.kill()
                proc.wait(timeout=2)

    def test_remote_job_status_preserves_session_id_job_alias(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import identity
            import mcp_server
            import remote_dev.mcp.tools as remote_tools

            listed = {item["name"]: item for item in remote_tools.list_tools()}
            self.assertIn("remote_job_status", listed)
            props = (listed["remote_job_status"].get("inputSchema") or {}).get(
                "properties"
            ) or {}
            self.assertIn("job_id", props)
            captured = {}

            def fake_call(name, args):
                captured["name"] = name
                captured["args"] = dict(args)
                captured["native"] = os.environ.get("REMOTE_DEV_SESSION_ID")
                captured["profile"] = os.environ.get("REMOTE_DEV_HARNESS_PROFILE")
                return {
                    "text": "ok",
                    "result": {"outcome": "success", "job_id": args.get("session_id")},
                }

            args = dict(session_id="job-alias-1", request_nonce="nonce-remote-job")
            identity.publish_nonce_bind(
                "ses_native_owner",
                "nonce-remote-job",
                "mcp__plugin-mindie-agent_remote__remote_job_status",
                "tool_remote_job",
                args,
            )
            original = remote_tools.call_tool
            remote_tools.call_tool = fake_call
            try:
                with patch.dict(os.environ, {"REMOTE_DEV_HARNESS_PROFILE": "codex"}):
                    response = mcp_server.handle(
                        "remote",
                        dict(
                            jsonrpc="2.0",
                            id=21,
                            method="tools/call",
                            params=dict(name="remote_job_status", arguments=args),
                        ),
                    )
            finally:
                remote_tools.call_tool = original
            self.assertFalse(response["result"]["isError"], response)
            self.assertEqual(captured["args"].get("session_id"), "job-alias-1")
            self.assertNotIn("request_nonce", captured["args"])
            self.assertEqual(captured["native"], "ses_native_owner")
            self.assertNotEqual(captured["native"], "job-alias-1")
            self.assertEqual(captured["profile"], "kimi")

            camel = mcp_server.handle(
                "remote",
                dict(
                    jsonrpc="2.0",
                    id=22,
                    method="tools/call",
                    params=dict(
                        name="remote_job_status",
                        arguments=dict(sessionId="ses_spoof", request_nonce="n2"),
                    ),
                ),
            )
            self.assertTrue(camel["result"]["isError"])

    def test_host_qualified_remote_name_strips_before_dispatch(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import identity
            import mcp_server
            import remote_dev.mcp.tools as remote_tools

            host_name = "mcp__plugin-mindie-agent_remote__remote_Read"
            args = {"path": "/remote/not-read", "request_nonce": "nonce-host-name"}
            identity.publish_nonce_bind(
                "ses_host_name", "nonce-host-name", host_name, "tool_host_name", args,
            )
            captured = {}

            def fake_call(name, call_args):
                captured["name"] = name
                captured["args"] = dict(call_args)
                return {"text": "ok", "result": {"outcome": "success"}}

            original = remote_tools.call_tool
            remote_tools.call_tool = fake_call
            try:
                response = mcp_server.handle(
                    "remote",
                    dict(jsonrpc="2.0", id=31, method="tools/call",
                         params=dict(name=host_name, arguments=args)),
                )
            finally:
                remote_tools.call_tool = original
            self.assertFalse(response["result"]["isError"], response)
            self.assertEqual(captured["name"], "remote_Read")
            self.assertNotIn("request_nonce", captured["args"])

    def test_remote_tools_follow_the_trusted_profile_env(self):
        import mcp_server
        with patch.dict(os.environ, {"REMOTE_DEV_HARNESS_PROFILE": "kimi"}):
            tools = {item["name"]: item for item in mcp_server.remote_tools()}
        self.assertIn("remote_read", tools)
        self.assertTrue(
            {"remote_Read", "remote_Write", "remote_Bash", "remote_TaskOutput",
             "remote_TaskStop", "remote_TaskList", "remote_ReadMediaFile"}
            <= set(tools)
        )
        self.assertNotIn("remote_exec_command", tools)
        for name in ("remote_read", "remote_Read", "remote_TaskList"):
            self.assertIn("request_nonce", tools[name]["inputSchema"]["required"])

    def test_remote_tools_are_listed_without_knowledge_activation(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            proc = self.start("remote", config)
            try:
                self.rpc(proc, dict(jsonrpc="2.0", id=1, method="initialize", params={}))
                listed = self.rpc(proc, dict(jsonrpc="2.0", id=2, method="tools/list"))
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("remote_read", names)
                self.assertIn("remote_job_status", names)
                for item in listed["result"]["tools"]:
                    self.assertIn("request_nonce", item["inputSchema"]["required"])
            finally:
                proc.stdin.close()
                proc.kill()
                proc.wait(timeout=2)

    def test_knowledge_distill_forwards_capture_lease_from_registered_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = self.config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import admission
            import identity
            import mcp_server

            lease = admission.activate("ses_distill", project_root=str(tmp.resolve()))
            args = dict(
                inputs=[{
                    "kind": "authorized_transcript",
                    "outcome": "in_flight",
                    "source_locator": "fixture@abcdef1:probe.txt",
                    "content": "in-flight fixture",
                    "evidence_refs": ["fixture-observation"],
                    "repetitions": 1,
                }],
                task_state="in_flight",
                request_nonce="nonce-distill1",
            )
            identity.publish_nonce_bind(
                "ses_distill",
                "nonce-distill1",
                "mcp__plugin-mindie-agent_knowledge__knowledge_distill",
                "tool_distill1",
                args,
            )
            captured = {}

            def fake_distill(path, payload):
                captured.update(path=path, payload=payload)
                return {"status": "noop", "model_calls": 0}

            with patch("knowledge_service.existing_service",
                       return_value={"url": "http://127.0.0.1:1", "token": "synthetic"}), \
                    patch("mindie_knowledge.loop.cli.distill_selected", fake_distill):
                response = mcp_server.handle("knowledge", dict(
                    jsonrpc="2.0", id=30, method="tools/call",
                    params=dict(name="knowledge_distill", arguments=args),
                ))
            self.assertFalse(response["result"]["isError"], response)
            self.assertEqual(response["result"]["structuredContent"]["status"], "noop")
            self.assertEqual(response["result"]["structuredContent"]["model_calls"], 0)
            self.assertEqual(response["result"]["structuredContent"]["operation_status"], "completed")
            self.assertEqual(captured["payload"], {
                "inputs": args["inputs"],
                "task_state": "in_flight",
                "_session_id": "ses_distill",
                "_activation": lease["token"],
            })
            self.assertNotIn("_session_verified", captured["payload"])
            self.assertNotIn("request_nonce", captured["payload"])

    def test_distill_status_preserves_paid_result_and_rejects_uncertain_execution(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ["MINDIE_KIMI_CONFIG"] = str(self.config(tmp))
            sys.path.insert(0, str(SCRIPTS))
            import admission
            import identity
            import mcp_server

            admission.activate("ses_distill", project_root=str(tmp.resolve()))
            for number, status in enumerate(("failed", "outcome_unknown", "invoking",
                                            "local_receipt_failed", "local_application_failed"), 50):
                preserved = status.startswith("local_")
                args = dict(inputs=[{"kind": "authorized_transcript", "outcome": "observed",
                                     "source_locator": "fixture@abcdef1:probe.txt", "content": "Fixture.",
                                     "evidence_refs": ["fixture-observation"]}],
                            task_state="completed", request_nonce=f"nonce-distill{number}")
                identity.publish_nonce_bind("ses_distill", args["request_nonce"],
                    "mcp__plugin-mindie-agent_knowledge__knowledge_distill", f"tool_distill{number}", args)
                payload = {"status": status, "attempt_id": "fixture-attempt", "model_replayed": False}
                if preserved:
                    payload.update(model_execution="completed", model_status="returned",
                        local_error={"stage": "receipt", "message": "controlled storage failure"},
                        result={"schema_version": "mindie-distill-decision/1", "session_digest": "a" * 64,
                                "evidence_inputs": [], "action": "noop", "drop_reason": "already_covered", "units": []})
                with self.subTest(status=status), \
                        patch("knowledge_service.existing_service", return_value={"url": "http://127.0.0.1:1", "token": "fixture"}), \
                        patch("mindie_knowledge.loop.cli.distill_selected", return_value=payload):
                    response = mcp_server.handle("knowledge", dict(jsonrpc="2.0", id=number, method="tools/call",
                        params=dict(name="knowledge_distill", arguments=args)))["result"]
                    self.assertIs(response["isError"], not preserved)
                    self.assertEqual(response["structuredContent"]["operation_status"], status)
                    if preserved:
                        self.assertEqual(response["structuredContent"]["result"], payload["result"])
                        self.assertIn("do not repeat", response["structuredContent"]["operation_message"])

    def test_knowledge_distill_unbound_never_starts_service_or_model(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = self.config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import mcp_server

            args = dict(
                inputs=[{
                    "kind": "authorized_transcript", "outcome": "in_flight",
                    "source_locator": "fixture@abcdef1:probe.txt",
                    "content": "in-flight fixture", "evidence_refs": ["fixture-observation"],
                }],
                task_state="in_flight",
            )
            with patch("knowledge_service.existing_service") as existing, \
                    patch("knowledge_service.ensure_service") as ensure, \
                    patch("mindie_knowledge.loop.cli.distill_selected") as distill:
                with self.assertRaisesRegex(ValueError, "not bound"):
                    mcp_server.knowledge_call("knowledge_distill", args, "ses_unbound")
                existing.assert_not_called()
                ensure.assert_not_called()
                distill.assert_not_called()

    def test_knowledge_query_forwards_graded_filters_to_shared_runtime(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = self.config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import admission
            import mcp_server

            admission.activate("ses_query", project_root=str(tmp.resolve()))
            captured = {}

            def fake_rpc(connection, method, payload, *, timeout=None):
                captured.update(method=method, payload=payload, timeout=timeout)
                return {"results": []}

            args = dict(
                query="fixture", domain_path="ops/probes",
                token_budget=123, include_unknown=True, include_archived=True,
            )
            with patch("knowledge_service.existing_service",
                       return_value={"url": "http://127.0.0.1:1", "token": "synthetic"}), \
                    patch("mindie_knowledge.loop.cli.rpc", fake_rpc):
                result = mcp_server.knowledge_call("knowledge_query", args, "ses_query")
            self.assertEqual(result, {"results": []})
            self.assertEqual(captured["method"], "query")
            self.assertEqual(captured["payload"]["domain_path"], "ops/probes")
            self.assertEqual(captured["payload"]["token_budget"], 123)
            self.assertIs(captured["payload"]["include_unknown"], True)
            self.assertIs(captured["payload"]["include_archived"], True)
            self.assertEqual(captured["payload"]["_session_id"], "ses_query")
            self.assertIs(captured["payload"]["_session_verified"], True)
            self.assertEqual(captured["timeout"], mcp_server.KNOWLEDGE_RPC_TIMEOUT)
            from urllib.error import URLError

            for timeout_exc in (TimeoutError("status probe timed out"), URLError(TimeoutError("status probe timed out"))):
                with self.subTest(timeout_exc=type(timeout_exc).__name__), \
                        patch("knowledge_service.existing_service", side_effect=timeout_exc), \
                        patch("knowledge_service.ensure_service") as ensure:
                    with self.assertRaises((TimeoutError, URLError)):
                        mcp_server.knowledge_call("knowledge_query", {"query": "fixture"}, "ses_query")
                    ensure.assert_not_called()

    def test_knowledge_explain_batch_forwards_closure_arguments(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = self.config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import admission
            import mcp_server

            admission.activate("ses_explain", project_root=str(tmp.resolve()))
            refs = [
                "mindie://vllm-ascend/units/unit-a@1",
                "mindie://vllm-ascend/units/unit-b@2",
            ]
            captured = {}

            def fake_rpc(connection, method, payload, *, timeout=None):
                captured.update(method=method, payload=payload, timeout=timeout)
                return {"units": []}

            args = dict(
                refs=refs,
                conditions={"soc": "Ascend910B"},
                known_refs=[refs[0]],
                token_budget=2048,
            )
            with patch("knowledge_service.existing_service",
                       return_value={"url": "http://127.0.0.1:1", "token": "synthetic"}), \
                    patch("mindie_knowledge.loop.cli.rpc", fake_rpc):
                result = mcp_server.knowledge_call("knowledge_explain", args, "ses_explain")
            self.assertEqual(result, {"units": []})
            self.assertEqual(captured["method"], "explain")
            self.assertEqual(captured["payload"]["refs"], refs)
            self.assertEqual(captured["payload"]["conditions"], {"soc": "Ascend910B"})
            self.assertEqual(captured["payload"]["known_refs"], [refs[0]])
            self.assertEqual(captured["payload"]["token_budget"], 2048)
            self.assertEqual(captured["payload"]["_session_id"], "ses_explain")
            self.assertNotIn("request_nonce", captured["payload"])

    def test_knowledge_explain_rejects_mixed_domain_authorities(self):
        with tempfile.TemporaryDirectory() as raw:
            config = self.config(Path(raw))
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import mcp_server

            args = {
                "refs": [
                    "mindie://vllm-ascend/units/unit-a@1",
                    "mindie://operators/units/unit-b@2",
                ],
            }
            with patch("knowledge_service.existing_service") as existing, \
                    patch("knowledge_service.ensure_service") as ensure, \
                    patch("mindie_knowledge.loop.cli.rpc") as rpc:
                with self.assertRaisesRegex(ValueError, "domain authorities"):
                    mcp_server.knowledge_call("knowledge_explain", args, "ses_explain")
                existing.assert_not_called()
                ensure.assert_not_called()
                rpc.assert_not_called()

    def test_knowledge_distill_forwards_related_refs_and_proposal(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = self.config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import admission
            import mcp_server

            lease = admission.activate("ses_distill_delta", project_root=str(tmp.resolve()))
            proposal = {
                "schema_version": "mindie-distill-decision/1",
                "session_digest": "a" * 64,
                "evidence_inputs": ["authorized_transcript"],
                "action": "noop",
                "drop_reason": "already_covered",
                "units": [],
            }
            args = dict(
                inputs=[{
                    "kind": "authorized_transcript",
                    "outcome": "verified",
                    "source_locator": "fixture@abcdef1:probe.txt",
                    "content": "verified fixture",
                    "evidence_refs": ["fixture-observation"],
                }],
                task_state="verified_milestone",
                related_refs=["unit-a", "mindie://vllm-ascend/units/unit-b@2"],
                proposal=proposal,
            )
            captured = {}

            def fake_rpc(connection, method, payload, *, timeout=None):
                captured.update(method=method, payload=payload, timeout=timeout)
                return {"status": "noop", "model_calls": 0}

            with patch("knowledge_service.existing_service",
                       return_value={"url": "http://127.0.0.1:1", "token": "synthetic"}), \
                    patch("mindie_knowledge.loop.cli.rpc", fake_rpc):
                result = mcp_server.knowledge_call(
                    "knowledge_distill", args, "ses_distill_delta"
                )
            self.assertEqual(result["status"], "noop")
            self.assertEqual(captured["method"], "distill")
            self.assertEqual(captured["payload"]["related_refs"], args["related_refs"])
            self.assertEqual(captured["payload"]["proposal"], proposal)
            self.assertEqual(captured["payload"]["_session_id"], "ses_distill_delta")
            self.assertEqual(captured["payload"]["_activation"], lease["token"])
            self.assertNotIn("request_nonce", captured["payload"])
            self.assertIsNotNone(captured["timeout"])

    def test_tool_arguments_reject_internal_identity_fields(self):
        import mcp_server

        self.assertEqual(
            {item["name"] for item in mcp_server.KNOWLEDGE_TOOLS},
            {
                "mindie_entry", "knowledge_query", "knowledge_explain",
                "knowledge_feedback", "knowledge_distill",
            },
        )
        tools = {item["name"]: item for item in mcp_server.KNOWLEDGE_TOOLS}
        self.assertTrue({
            "domain_path", "token_budget", "include_unknown", "include_archived",
        } <= set(tools["knowledge_query"]["inputSchema"]["properties"]))
        self.assertEqual(
            set(tools["knowledge_distill"]["inputSchema"]["properties"]),
            {"inputs", "task_state", "related_refs", "proposal", "domain", "request_nonce"},
        )
        for field in ("_session_id", "_session_verified", "_activation", "mindie_activation"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                mcp_server.reject_native_identity_args({field: "spoof"}, "knowledge")

    def test_unconfigured_mindie_entry_init_returns_choices(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            home = tmp / "kimi-home"
            xdg = tmp / "xdg"
            write_session(
                home,
                "ses_entry",
                turn_records(plugin_origin("init", "act-entry"), "init"),
                cwd=tmp,
            )
            missing = tmp / "absent.json"
            os.environ["MINDIE_KIMI_CONFIG"] = str(missing)
            os.environ["KIMI_CODE_HOME"] = str(home)
            os.environ["XDG_CONFIG_HOME"] = str(xdg)
            sys.path.insert(0, str(SCRIPTS))
            import identity

            args = dict(op="init", request_nonce="nonce-entry1")
            identity.publish_nonce_bind(
                "ses_entry",
                "nonce-entry1",
                "mcp__plugin-mindie-agent_knowledge__mindie_entry",
                "tool_entry",
                args,
            )
            proc = self.start(
                "knowledge",
                missing,
                extra={
                    "KIMI_CODE_HOME": str(home),
                    "MINDIE_KIMI_CONFIG": str(missing),
                    "XDG_CONFIG_HOME": str(xdg),
                },
            )
            try:
                self.rpc(proc, dict(jsonrpc="2.0", id=1, method="initialize", params={}))
                listed = self.rpc(proc, dict(jsonrpc="2.0", id=2, method="tools/list"))
                names = {item["name"] for item in listed["result"]["tools"]}
                self.assertIn("mindie_entry", names)
                result = self.rpc(
                    proc,
                    dict(
                        jsonrpc="2.0",
                        id=3,
                        method="tools/call",
                        params=dict(name="mindie_entry", arguments=args),
                    ),
                )
                self.assertFalse(result["result"]["isError"], result["result"])
                payload = result["result"]["structuredContent"]
                self.assertFalse(payload["configured"])
                self.assertEqual(payload["choices"], [])
                self.assertEqual(payload["experience"], "needs-configuration")
                self.assertFalse(payload["sharing"]["enabled"])
            finally:
                proc.stdin.close()
                proc.kill()
                proc.wait(timeout=2)

    def test_once_tools_list_does_not_require_initialize(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            req = dict(jsonrpc="2.0", id=7, method="tools/list")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "mcp_server.py"), "knowledge", "--once"],
                input=json.dumps(req) + "\n",
                text=True, encoding="utf-8",
                capture_output=True,
                timeout=8,
                env=env_for(config),
                cwd=str(SCRIPTS.parent),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            lines = [line for line in result.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1, result.stdout)
            payload = json.loads(lines[0])
            self.assertEqual(payload["id"], 7)
            names = {item["name"] for item in payload["result"]["tools"]}
            self.assertIn("mindie_entry", names)

    def test_once_rejects_replayed_nonce(self):
        with tempfile.TemporaryDirectory() as raw:
            config = make_config(Path(raw))
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import identity

            args = dict(query="npu", request_nonce="nonce-once1")
            identity.publish_nonce_bind(
                "ses_once",
                "nonce-once1",
                "mcp__plugin-mindie-agent_knowledge__knowledge_query",
                "tool_once",
                args,
            )
            env = env_for(config)
            first = subprocess.run(
                [sys.executable, str(SCRIPTS / "mcp_server.py"), "knowledge", "--once"],
                input=json.dumps(
                    dict(
                        jsonrpc="2.0",
                        id=11,
                        method="tools/call",
                        params=dict(name="knowledge_query", arguments=args),
                    )
                )
                + "\n",
                text=True, encoding="utf-8",
                capture_output=True,
                timeout=8,
                env=env,
                cwd=str(SCRIPTS.parent),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            first_payload = json.loads(first.stdout.strip().splitlines()[0])
            self.assertEqual(first_payload["id"], 11)
            second = subprocess.run(
                [sys.executable, str(SCRIPTS / "mcp_server.py"), "knowledge", "--once"],
                input=json.dumps(
                    dict(
                        jsonrpc="2.0",
                        id=12,
                        method="tools/call",
                        params=dict(name="knowledge_query", arguments=args),
                    )
                )
                + "\n",
                text=True, encoding="utf-8",
                capture_output=True,
                timeout=8,
                env=env,
                cwd=str(SCRIPTS.parent),
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            lines = [line for line in second.stdout.splitlines() if line.strip()]
            self.assertEqual(len(lines), 1, second.stdout)
            payload = json.loads(lines[0])
            self.assertEqual(payload["id"], 12)
            self.assertTrue(payload["result"]["isError"])
            self.assertIn("replay", payload["result"]["content"][0]["text"].lower())


class RemoteObservationBoundTests(unittest.TestCase):
    def test_only_poll_yield_is_capped(self):
        import mcp_server
        args = {"yield_time_ms": 300000, "timeout_ms": 600000}
        self.assertEqual(mcp_server.clamp_remote_args(args),
                         {"yield_time_ms": 30000, "timeout_ms": 600000})
        self.assertEqual(args["yield_time_ms"], 300000)
        for invalid in ("300000", True, 1.5):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                mcp_server.clamp_remote_args({"yield_time_ms": invalid})


class RemoteFailureProjectionTests(unittest.TestCase):
    def test_only_typed_fields_survive(self):
        import mcp_server
        from remote_dev.core.errors import RemoteExecutionError, caller_error
        result = mcp_server.remote_stage_failure(
            {"job_id": "original-job"}, "remote_job_status", "helper_failed",
            RemoteExecutionError("PRIVATE_SENTINEL", category="rpc_timeout",
                                 submission_state="uncertain", retryable=False))
        self.assertEqual(result["structuredContent"], {
            "stage": "helper_failed", "remote_outcome": "unconfirmed",
            "job_id": "original-job", "category": "rpc_timeout",
            "submission_state": "uncertain", "retryable": False})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))
        result = mcp_server.remote_stage_failure(
            {}, "remote_bash", "helper_failed", caller_error("PRIVATE_SENTINEL"))
        self.assertEqual(result["structuredContent"]["remote_outcome"], "not_sent")
        self.assertNotIn("unconfirmed", json.dumps(result))
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(result))

    def test_unrecognized_category_cannot_expose_lowercase_secret(self):
        import mcp_server
        from remote_dev.core.errors import RemoteExecutionError
        result = mcp_server.remote_stage_failure(
            {}, "remote_bash", "helper_failed",
            RemoteExecutionError("other_secret", category="lower_case_secret"))
        self.assertEqual(result["structuredContent"]["category"], "internal")
        self.assertNotIn("secret", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
