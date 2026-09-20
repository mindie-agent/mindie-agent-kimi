import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import FORK_SESSION, PROBE_HOME, PROBE_SESSION, SCRIPTS, write_session

sys.path.insert(0, str(SCRIPTS))
import transcript  # noqa: E402


def rec(kind, **fields):
    fields.setdefault("time", 2_000_000_000_000)
    fields["type"] = kind
    return fields


class TranscriptTests(unittest.TestCase):
    def test_real_probe_after_source_end_and_fork_are_empty(self):
        src = (
            PROBE_HOME
            / "sessions"
            / "wd_kimi-native-envelope_e6f97298a560"
            / PROBE_SESSION
            / "agents"
            / "main"
            / "wire.jsonl"
        )
        fork = (
            PROBE_HOME
            / "sessions"
            / "wd_kimi-native-envelope_e6f97298a560"
            / FORK_SESSION
            / "agents"
            / "main"
            / "wire.jsonl"
        )
        self.assertTrue(src.is_file(), "root probe wire missing")
        after = 1789911728.727
        source = transcript.read_material(str(src), 0, session_id=PROBE_SESSION, not_before=after)
        self.assertEqual(source["records"], 0, source.get("text", "")[:200])
        forked = transcript.read_material(str(fork), 0, session_id=FORK_SESSION)
        self.assertEqual(forked["records"], 0, forked.get("text", "")[:200])
        public = transcript.read_material(
            str(src), 0, session_id=PROBE_SESSION, not_before=1789911712
        )
        self.assertEqual(public["records"], 6, public.get("text", "")[:200])
        self.assertIn("call_id=", public["text"])

    def test_public_user_allowlist_skips_injection_and_think(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = [
                rec("metadata", protocol_version="1.5", created_at=1_500_000_000_000),
                rec(
                    "context.append_message",
                    message=dict(
                        role="user",
                        content=[dict(type="text", text="fix the npu hang")],
                        origin=dict(kind="user"),
                    ),
                    time=2_000_000_000_000,
                ),
                rec(
                    "context.append_message",
                    message=dict(
                        role="user",
                        content=[dict(type="text", text="Today's date is secret")],
                        origin=dict(kind="injection", variant="date_change"),
                    ),
                    time=2_000_000_000_100,
                ),
                rec(
                    "context.append_loop_event",
                    event=dict(type="content.part", part=dict(type="think", think="secret chain")),
                    time=2_000_000_000_200,
                ),
                rec(
                    "context.append_loop_event",
                    event=dict(type="content.part", part=dict(type="text", text="try CANN log")),
                    time=2_000_000_000_300,
                ),
            ]
            _root, wire = write_session(home, "ses_t", records, state=dict(createdAt=1_000_000_000_000))
            result = transcript.read_material(str(wire), 0, session_id="ses_t")
            self.assertIn("fix the npu hang", result["text"])
            self.assertIn("try CANN log", result["text"])
            self.assertNotIn("Today's date", result["text"])
            self.assertNotIn("secret chain", result["text"])

    def test_ms_not_before_filters_old_records(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = [
                rec(
                    "context.append_message",
                    message=dict(
                        role="user",
                        content=[dict(type="text", text="old work")],
                        origin=dict(kind="user"),
                    ),
                    time=1_789_911_712_808,
                )
            ]
            _root, wire = write_session(home, "ses_old", records)
            result = transcript.read_material(
                str(wire), 0, session_id="ses_old", not_before=1789911728.727
            )
            self.assertEqual(result["records"], 0)
            self.assertNotIn("old work", result["text"])

    def test_unknown_format(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            _root, wire = write_session(home, "ses_u", [{"hello": "codex-rollout"}])
            result = transcript.read_material(str(wire), 0, session_id="ses_u")
            self.assertEqual(result["status"], "unknown-format")
            self.assertEqual(result["text"], "")
