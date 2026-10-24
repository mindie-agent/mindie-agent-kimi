import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from support import FIXTURES, SCRIPTS, plugin_origin, turn_records, write_session

sys.path.insert(0, str(SCRIPTS))
import identity  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


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

    def test_plugin_command_without_command_args_still_binds(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            origin = plugin_origin("status", "act-nocargs")
            del origin["commandArgs"]
            records = turn_records(origin, "status", time=1)
            write_session(home, "ses_nocargs", records)
            found = identity.require_current_plugin_command(
                "ses_nocargs", "status", kimi_home=home
            )
            self.assertEqual(found["command"], "status")
            self.assertEqual(found["arguments"], "")
            self.assertEqual(found["activation_id"], "act-nocargs")


def _substitute_plugin_root(text: str, root) -> str:
    """Splice the repo path into JSONL fixture text. The placeholder sits
    inside a JSON string, so the path is spliced in its JSON-escaped form —
    a Windows path's backslashes must not become invalid JSON escapes."""
    return text.replace("__PLUGIN_ROOT__", json.dumps(str(root))[1:-1])


class SkillActivationShapeTests(unittest.TestCase):
    """kimi 2.1.1 top-level vs kimi 2.2.0 nested skill-activation wire forms."""

    def _fixture_home(self, tmp: Path, fixture_session: str, session: str) -> Path:
        src = (
            FIXTURES
            / "native-shaped"
            / "sessions"
            / "wd_fixture"
            / fixture_session
        )
        home = tmp / "kimi-home"
        dest = home / "sessions" / "wd_fixture" / session
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
        wire = dest / "agents" / "main" / "wire.jsonl"
        wire.write_text(
            _substitute_plugin_root(wire.read_text(encoding="utf-8"), REPO),
            encoding="utf-8",
        )
        return home

    def test_fixture_substitution_produces_valid_json_with_windows_path(self):
        root = "C:\\Users\\runner\\mindie-agent-kimi"
        for session in (
            "ses_nested_220",
            "ses_nested_steer",
            "ses_nested_ambiguous",
            "ses_nested_foreign",
        ):
            wire = (
                FIXTURES
                / "native-shaped"
                / "sessions"
                / "wd_fixture"
                / session
                / "agents"
                / "main"
                / "wire.jsonl"
            )
            substituted = _substitute_plugin_root(
                wire.read_text(encoding="utf-8"), root
            )
            skill_paths = []

            def walk(value):
                if isinstance(value, dict):
                    if isinstance(value.get("skillPath"), str):
                        skill_paths.append(value["skillPath"])
                    for item in value.values():
                        walk(item)
                elif isinstance(value, list):
                    for item in value:
                        walk(item)

            for line in substituted.splitlines():
                walk(json.loads(line))
            self.assertTrue(skill_paths, session)
            for path in skill_paths:
                self.assertTrue(path.startswith(root), (session, path))

    def test_nested_220_activation_binds_the_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._fixture_home(Path(raw), "ses_nested_220", "ses_nested_220")
            found = identity.require_current_entry(
                "ses_nested_220", "init", kimi_home=home
            )
            self.assertEqual(found["command"], "init")
            self.assertEqual(found["arguments"], "init")
            self.assertEqual(
                found["activation_id"], "d8cf954f-6b26-49cc-b019-606640c397d5"
            )
            # The nested turn is not an ordinary user reply either.
            self.assertEqual(
                identity.current_user_text("ses_nested_220", kimi_home=home), ""
            )

    def test_top_level_211_activation_still_binds(self):
        from contract_support import skill_origin

        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = turn_records(
                skill_origin("act-211", args="init"), "/mindie-agent init"
            )
            write_session(home, "ses_211", records)
            found = identity.require_current_entry("ses_211", "init", kimi_home=home)
            self.assertEqual(found["activation_id"], "act-211")
            self.assertEqual(found["arguments"], "init")

    def test_ambiguous_nested_activations_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._fixture_home(
                Path(raw), "ses_nested_ambiguous", "ses_nested_ambiguous"
            )
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                identity.require_current_entry(
                    "ses_nested_ambiguous", "init", kimi_home=home
                )

    def test_foreign_nested_skill_is_not_the_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._fixture_home(
                Path(raw), "ses_nested_foreign", "ses_nested_foreign"
            )
            with self.assertRaises(ValueError):
                identity.require_current_entry(
                    "ses_nested_foreign", "init", kimi_home=home
                )
            # A foreign skill activation is an ordinary user message.
            self.assertEqual(
                identity.current_user_text("ses_nested_foreign", kimi_home=home),
                "/mindie-agent init",
            )

    def test_steering_nested_activation_is_not_an_opener(self):
        with tempfile.TemporaryDirectory() as raw:
            home = self._fixture_home(Path(raw), "ses_nested_steer", "ses_nested_steer")
            with self.assertRaisesRegex(ValueError, "in-turn nested"):
                identity.require_current_entry(
                    "ses_nested_steer", "init", kimi_home=home
                )


class NativeUserEntryTests(unittest.TestCase):
    def test_current_native_user_prefix_authorizes_without_skill_expansion(self):
        # Kimi 2.3.1 native turn.prompt/context.append_message envelope.
        prompt = dict(type="turn.prompt", promptId="msg_native_231", turnId=0,
                      origin={"kind": "user"}, input=[{"type": "text", "text":
                          "/mindie-agent initialize entry only"}], time=1792840620684)
        appended = dict(type="context.append_message", time=1792840620684,
                        message=dict(role="user", id="msg_native_231", origin={"kind": "user"},
                                     content=prompt["input"]))
        for record in [prompt, appended]:
            with self.subTest(record=record["type"]):
                result = identity._entry_from_openings([(record, {"kind": "user"})], "init")
                self.assertEqual(result, dict(command="init", arguments="initialize entry only",
                                              activation_id="user-entry:msg_native_231"))
        for text in ["please run /mindie-agent", "`/mindie-agent`", "/mindie-agent-other"]:
            record = dict(prompt, input=[{"type": "text", "text": text}])
            with self.subTest(text=text), self.assertRaises(ValueError):
                identity._entry_from_openings([(record, {"kind": "user"})], "init")
        with self.assertRaises(ValueError):
            identity._entry_from_openings([(prompt, {"kind": "injection"})], "init")
        with self.assertRaises(ValueError):
            identity._entry_from_openings([(prompt, {"kind": "user"})], "choose")
