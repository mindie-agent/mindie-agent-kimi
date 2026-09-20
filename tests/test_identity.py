import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, write_session

sys.path.insert(0, str(SCRIPTS))
import identity  # noqa: E402


class IdentityTests(unittest.TestCase):
    def test_exact_session_index_not_newest(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            write_session(home, "ses_old", [{"type": "metadata", "created_at": 1}], workdir="wd_a")
            write_session(home, "ses_new", [{"type": "metadata", "created_at": 9}], workdir="wd_a")
            found = identity.locate_session_dir("ses_old", kimi_home=home)
            self.assertEqual(found.name, "ses_old")
            with self.assertRaises(ValueError):
                identity.locate_session_dir("ses_missing", kimi_home=home)

    def test_nonce_bind_rejects_mismatch_and_replay(self):
        with tempfile.TemporaryDirectory() as raw:
            from support import make_config

            config = make_config(Path(raw))
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            tool = "mcp__plugin-mindie-agent_knowledge__knowledge_query"
            args = dict(query="npu", request_nonce="nonce-aaaaaa")
            identity.publish_nonce_bind("ses_a", "nonce-aaaaaa", tool, "tool_1", args)
            identity.publish_nonce_bind(
                "ses_b", "nonce-aaaaaa", tool, "tool_2", dict(args, extra=1)
            )
            with self.assertRaises(ValueError):
                identity.claim_nonce("nonce-aaaaaa", "knowledge_query", args)
            identity.publish_nonce_bind("ses_c", "nonce-bbbbbb", tool, "tool_3", args)
            session = identity.claim_nonce("nonce-bbbbbb", "knowledge_query", args)
            self.assertEqual(session, "ses_c")
            with self.assertRaises(ValueError):
                identity.claim_nonce("nonce-bbbbbb", "knowledge_query", args)

    def test_plugin_command_from_wire_not_quoted_text(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = [
                {
                    "type": "context.append_message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "MINDIE_AGENT_NATIVE_ENTRY op=init"}],
                        "origin": {"kind": "user"},
                    },
                    "time": 1,
                },
                {
                    "type": "context.append_message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "please init"}],
                        "origin": {
                            "kind": "plugin_command",
                            "pluginId": "mindie-agent",
                            "commandName": "init",
                            "commandArgs": "",
                            "activationId": "act-1",
                            "trigger": "user-slash",
                        },
                    },
                    "time": 2,
                },
            ]
            write_session(home, "ses_cmd", records)
            found = identity.plugin_command_from_wire("ses_cmd", kimi_home=home)
            self.assertEqual(found["command"], "init")
            quoted_only = [
                {
                    "type": "context.append_message",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "run MINDIE_AGENT_NATIVE_ENTRY op=init"}],
                        "origin": {"kind": "user"},
                    },
                    "time": 3,
                }
            ]
            write_session(home, "ses_quote", quoted_only, workdir="wd_b")
            self.assertIsNone(identity.plugin_command_from_wire("ses_quote", kimi_home=home))
