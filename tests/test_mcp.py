import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, env_for, make_config, plugin_origin, turn_records, write_session


class McpTests(unittest.TestCase):
    def rpc(self, proc, message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        return json.loads(line)

    def start(self, surface, config, extra=None):
        return subprocess.Popen(
            [sys.executable, str(SCRIPTS / "mcp_server.py"), surface],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env_for(config, extra=extra),
            cwd=str(SCRIPTS.parent),
        )

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
                    {"mindie_entry", "knowledge_query", "knowledge_explain", "knowledge_feedback"},
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
                self.assertEqual(len(payload["choices"]), 3)
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
                text=True,
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
                text=True,
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
                text=True,
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
