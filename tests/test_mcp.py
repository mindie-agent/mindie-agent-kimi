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
