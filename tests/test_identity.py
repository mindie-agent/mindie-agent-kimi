import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, plugin_origin, turn_records, write_session

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

    def test_current_turn_origin_not_last_matching_command(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = turn_records(plugin_origin("init", "act-old"), "init", time=1)
            records.extend(
                turn_records(
                    dict(
                        kind="plugin_command",
                        pluginId="other-plugin",
                        commandName="foo",
                        commandArgs="",
                        activationId="act-other",
                        trigger="user-slash",
                    ),
                    "foo",
                    time=3,
                )
            )
            write_session(home, "ses_cmd", records)
            origin = identity.current_turn_origin("ses_cmd", kimi_home=home)
            self.assertEqual(origin.get("pluginId"), "other-plugin")
            with self.assertRaises(ValueError):
                identity.require_current_plugin_command("ses_cmd", "init", kimi_home=home)
            quoted_only = turn_records(dict(kind="user"), "run /mindie-agent:init", time=4)
            write_session(home, "ses_quote", quoted_only, workdir="wd_b")
            origin = identity.current_turn_origin("ses_quote", kimi_home=home)
            self.assertEqual(origin.get("kind"), "user")
            with self.assertRaises(ValueError):
                identity.require_current_plugin_command("ses_quote", "init", kimi_home=home)

    def test_session_index_read_is_bounded(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            write_session(home, "ses_bound", [{"type": "metadata", "created_at": 1}])
            index = home / "session_index.jsonl"
            index.write_bytes(b"x" * (8 * 1024 * 1024 + 64))
            found = identity.locate_session_dir("ses_bound", kimi_home=home)
            self.assertEqual(found.name, "ses_bound")

    def test_require_current_plugin_command_uses_latest_opening(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = turn_records(plugin_origin("init", "act-1"), "init", time=1)
            write_session(home, "ses_ok", records)
            found = identity.require_current_plugin_command("ses_ok", "init", kimi_home=home)
            self.assertEqual(found["command"], "init")
            self.assertEqual(found["activation_id"], "act-1")
