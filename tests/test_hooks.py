import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, install_inspect_shim, make_config, run_bridge, write_session

sys.path.insert(0, str(SCRIPTS))


class HookTests(unittest.TestCase):
    def setUp(self):
        install_inspect_shim()

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

    def _run_stop_in_process(self, config, event, captured, result=None):
        import types

        fake = types.ModuleType("mindie_knowledge.loop.cli")

        def capture_hook(config_path, payload):
            captured.append((config_path, payload))
            return result if result is not None else {"stage": "accepted-local", "wake": "not-requested"}

        fake.capture_hook = capture_hook
        import bridge

        class FakeStdin:
            buffer = None

        stdin = FakeStdin()
        import io

        stdin.buffer = io.BytesIO(json.dumps(event).encode())
        old_stdin, old_stdout = sys.stdin, sys.stdout
        old_cli = sys.modules.get("mindie_knowledge.loop.cli")
        sys.modules["mindie_knowledge.loop.cli"] = fake
        printed = []
        old_print = bridge._print
        try:
            sys.stdin = stdin
            sys.stdout = io.StringIO()
            bridge._print = printed.append
            rc = bridge.handle_stop()
        finally:
            sys.stdin, sys.stdout = old_stdin, old_stdout
            bridge._print = old_print
            if old_cli is not None:
                sys.modules["mindie_knowledge.loop.cli"] = old_cli
            else:
                sys.modules.pop("mindie_knowledge.loop.cli", None)
        self.assertEqual(rc, 0)
        self.assertEqual(printed, [{}])

    def test_stop_hands_transcript_notification_with_local_event_id(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            home = tmp / "kimi-home"
            write_session(home, "ses_ev", [{"type": "metadata", "created_at": 1}])
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["KIMI_CODE_HOME"] = str(home)
            import admission as admission_mod

            lease = admission_mod.activate("ses_ev", project_root=str(tmp.resolve()))
            captured = []
            self._run_stop_in_process(
                config,
                {
                    "hook_event_name": "Stop",
                    "session_id": "ses_ev",
                    "cwd": str(tmp),
                    "stop_hook_active": False,
                },
                captured,
            )
            self.assertEqual(len(captured), 1)
            _path, event = captured[0]
            self.assertEqual(event["harness"], "kimi")
            self.assertEqual(event["identity_kind"], "notification")
            self.assertEqual(event["session_id"], "ses_ev")
            self.assertEqual(event["mindie_activation"], lease["token"])
            self.assertRegex(event["event_id"], r"^[0-9a-f]{32}$")
            self.assertNotIn("turn_id", event)
            self.assertTrue(event["transcript_path"].endswith("wire.jsonl"))
            self.assertNotIn("summary", event)
            fresh = admission_mod.gate().check("ses_ev")
            self.assertEqual(fresh.get("failures", 0), 0)

    def test_stop_inactive_session_is_quiet_no_notification(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(diag)
            captured = []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_never", "cwd": str(tmp)},
                captured,
            )
            self.assertEqual(captured, [])
            self.assertEqual(self._diagnostic_events(diag), [])

    def test_stop_unreadable_admission_is_recorded_not_inactive(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(diag)
            import admission as admission_mod
            from paths import admission_path

            admission_mod.activate("ses_lock", project_root=str(tmp.resolve()))
            admission_path().write_bytes(b"not a sqlite database")
            captured = []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_lock", "cwd": str(tmp)},
                captured,
            )
            self.assertEqual(captured, [])
            events = self._diagnostic_events(diag)
            self.assertTrue(
                any("admission-unreadable" in event for event in events), events
            )

    def test_stop_malformed_sharing_config_is_recorded(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(diag)
            import admission as admission_mod
            from paths import community_config_path

            admission_mod.activate("ses_cfg", project_root=str(tmp.resolve()))
            community_config_path().write_text("{ not json")
            captured = []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_cfg", "cwd": str(tmp)},
                captured,
            )
            self.assertEqual(captured, [])
            events = self._diagnostic_events(diag)
            self.assertTrue(any("configuration" in event for event in events), events)

    def test_stop_failed_handoff_stage_is_recorded(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(diag)
            import admission as admission_mod

            admission_mod.activate("ses_stage", project_root=str(tmp.resolve()))
            captured = []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_stage", "cwd": str(tmp)},
                captured,
                result={"stage": "unavailable", "cause": "store-locked"},
            )
            self.assertEqual(len(captured), 1)
            events = self._diagnostic_events(diag)
            self.assertTrue(any("store-locked" in event for event in events), events)

    def test_stop_without_locatable_wire_still_notifies(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ.pop("KIMI_CODE_HOME", None)
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            import admission as admission_mod

            admission_mod.activate("ses_nowire", project_root=str(tmp.resolve()))
            captured = []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_nowire", "cwd": str(tmp)},
                captured,
            )
            self.assertEqual(len(captured), 1)
            _path, event = captured[0]
            self.assertRegex(event["event_id"], r"^[0-9a-f]{32}$")
            self.assertNotIn("transcript_path", event)
            self.assertNotIn("turn_id", event)

    def test_stop_event_id_is_fresh_per_invocation(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            import admission as admission_mod

            admission_mod.activate("ses_dupe", project_root=str(tmp.resolve()))
            first, second = [], []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_dupe", "cwd": str(tmp)},
                first,
            )
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_dupe", "cwd": str(tmp)},
                second,
            )
            self.assertNotEqual(first[0][1]["event_id"], second[0][1]["event_id"])

    def _diagnostic_events(self, root):
        events = []
        for path in Path(root).rglob("*.jsonl"):
            try:
                events.extend(path.read_text().splitlines())
            except OSError:
                pass
        return events

    def test_stop_paused_lease_is_recorded_not_inactive(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            diag = Path(raw).resolve() / "diag"
            config = make_config(tmp, sharing=True, roots=[tmp])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(diag)
            import sqlite3

            import admission as admission_mod
            from paths import admission_path

            admission_mod.activate("ses_paused", project_root=str(tmp.resolve()))
            db = sqlite3.connect(admission_path())
            db.execute("UPDATE leases SET failures=3 WHERE session='ses_paused'")
            db.commit()
            db.close()
            captured = []
            self._run_stop_in_process(
                config,
                {"hook_event_name": "Stop", "session_id": "ses_paused", "cwd": str(tmp)},
                captured,
            )
            self.assertEqual(captured, [])
            events = self._diagnostic_events(diag)
            self.assertTrue(any("admission-paused" in event for event in events), events)
