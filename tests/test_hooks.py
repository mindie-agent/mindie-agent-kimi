import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, make_config, run_bridge, write_session

sys.path.insert(0, str(SCRIPTS))


class HookTests(unittest.TestCase):
    def test_stop_missing_config_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            config = Path(raw) / "absent.json"
            result = run_bridge(
                "stop",
                {"hook_event_name": "Stop", "session_id": "ses_x", "cwd": raw},
                config,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertEqual(list(Path(raw).iterdir()), [])

    def test_stop_never_exits_2_and_does_not_start_service(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            import admission as admission_mod

            admission_mod.activate("ses_live", project_root=str(tmp.resolve()))
            result = run_bridge(
                "stop",
                {
                    "hook_event_name": "Stop",
                    "session_id": "ses_live",
                    "cwd": str(tmp),
                    "stop_hook_active": False,
                    "turn_id": 0,
                },
                config,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertFalse((tmp / "domain" / "vllm-ascend" / "connection.json").exists())

    def test_turn_started_does_not_replay_plugin_command(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            home = tmp / "kimi-home"
            write_session(
                home,
                "ses_hook",
                [
                    {
                        "type": "context.append_message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "init"}],
                            "origin": {
                                "kind": "plugin_command",
                                "pluginId": "mindie-agent",
                                "commandName": "init",
                                "commandArgs": "",
                                "activationId": "act-hook",
                                "trigger": "user-slash",
                            },
                        },
                        "time": 2,
                    }
                ],
            )
            started = run_bridge(
                "turn-started",
                {
                    "hook_event_name": "TurnStarted",
                    "session_id": "ses_hook",
                    "cwd": str(tmp),
                    "origin_kind": "plugin_command",
                    "turn_id": 0,
                },
                config,
                kimi_home=home,
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(json.loads(started.stdout), {})
            leftover = run_bridge(
                "command",
                {
                    "hook_event_name": "TurnStarted",
                    "session_id": "ses_hook",
                    "cwd": str(tmp),
                    "origin_kind": "plugin_command",
                    "turn_id": 0,
                },
                config,
                kimi_home=home,
            )
            self.assertEqual(leftover.returncode, 0)
            self.assertEqual(json.loads(leftover.stdout), {})

    def test_quoted_marker_user_origin_does_not_activate(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            home = tmp / "kimi-home"
            write_session(
                home,
                "ses_quote",
                [
                    {
                        "type": "context.append_message",
                        "message": {
                            "role": "user",
                            "content": [{"type": "text", "text": "MINDIE_AGENT_NATIVE_ENTRY op=init"}],
                            "origin": {"kind": "user"},
                        },
                        "time": 1,
                    }
                ],
            )
            result = run_bridge(
                "turn-started",
                {
                    "hook_event_name": "TurnStarted",
                    "session_id": "ses_quote",
                    "cwd": str(tmp),
                    "origin_kind": "user",
                    "turn_id": 0,
                },
                config,
                kimi_home=home,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {})

    def test_pretool_binds_qualified_tool_and_arguments(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            event = {
                "hook_event_name": "PreToolUse",
                "session_id": "ses_native",
                "cwd": str(tmp),
                "tool_name": "mcp__plugin-mindie-agent_knowledge__knowledge_query",
                "tool_call_id": "tool_abc",
                "tool_input": {"query": "npu", "request_nonce": "nonce-zzzzzz"},
            }
            result = run_bridge("pretool", event, config)
            self.assertEqual(result.returncode, 0)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            import identity

            session = identity.claim_nonce(
                "nonce-zzzzzz",
                "knowledge_query",
                {"query": "npu", "request_nonce": "nonce-zzzzzz"},
            )
            self.assertEqual(session, "ses_native")
