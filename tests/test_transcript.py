import json
import sys
import tempfile
import unittest
from pathlib import Path

from support import FIXTURES, SCRIPTS, write_session

sys.path.insert(0, str(SCRIPTS))
import transcript  # noqa: E402


def rec(kind, **fields):
    fields.setdefault("time", 2_000_000_000_000)
    fields["type"] = kind
    return fields


class TranscriptTests(unittest.TestCase):
    def test_native_shaped_fork_uses_state_created_at_not_inherited_metadata(self):
        home = FIXTURES / "native-shaped"
        src = home / "sessions" / "wd_fixture" / "ses_source" / "agents" / "main" / "wire.jsonl"
        fork = home / "sessions" / "wd_fixture" / "ses_fork" / "agents" / "main" / "wire.jsonl"
        after_source_end = 1789911728.727
        source = transcript.read_material(
            str(src), 0, session_id="ses_source", not_before=after_source_end
        )
        self.assertEqual(source["records"], 0, source.get("text", "")[:200])
        forked = transcript.read_material(str(fork), 0, session_id="ses_fork")
        self.assertEqual(forked["records"], 0, forked.get("text", "")[:200])
        public = transcript.read_material(
            str(src), 0, session_id="ses_source", not_before=1789911712
        )
        self.assertGreaterEqual(public["records"], 1, public.get("text", "")[:200])
        self.assertIn("call_id=", public["text"])
        self.assertIn("fix the hang", public["text"])

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

    def test_continuation_page_without_recognized_records_is_not_unknown_format(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            known = rec(
                "context.append_message",
                message=dict(
                    role="user",
                    content=[dict(type="text", text="first page")],
                    origin=dict(kind="user"),
                ),
            )
            filler = [rec("llm.request", model="k", turnStep="1.%d" % i) for i in range(8)]
            _root, wire = write_session(
                home, "ses_cont", [known, *filler], state=dict(createdAt=1_000_000_000_000)
            )
            cursor = len(json.dumps(known)) + 1
            page = transcript.read_material(str(wire), cursor, session_id="ses_cont")
            self.assertEqual(page["status"], "ok", page.get("coverage_note"))
            self.assertEqual(page["records"], 0)
            self.assertGreater(page["end"], cursor)
            self.assertFalse(page["more"])
            last = transcript.read_material(str(wire), page["end"], session_id="ses_cont")
            self.assertEqual(last["status"], "unchanged")

    def test_trailing_partial_record_is_pending_not_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            _root, wire = write_session(
                home, "ses_part", [rec("metadata", created_at=1_500_000_000_000)]
            )
            first = transcript.read_material(str(wire), 0, session_id="ses_part")
            cursor = first["end"]
            with wire.open("a") as stream:
                stream.write('{"type":"context.append_message","message":{"role":"user"')
            pending = transcript.read_material(str(wire), cursor, session_id="ses_part")
            self.assertEqual(pending["status"], "ok")
            self.assertTrue(pending["partial"])
            self.assertTrue(pending["more"])
            self.assertEqual(pending["end"], cursor)
            self.assertEqual(pending["records"], 0)

    def test_trailing_partial_after_consumed_records_keeps_safe_cursor(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            _root, wire = write_session(
                home, "ses_tail", [rec("metadata", created_at=1_500_000_000_000)]
            )
            first = transcript.read_material(str(wire), 0, session_id="ses_tail")
            cursor = first["end"]
            complete = rec(
                "context.append_message",
                message=dict(
                    role="user",
                    content=[dict(type="text", text="new complete work")],
                    origin=dict(kind="user"),
                ),
            )
            with wire.open("a") as stream:
                stream.write(json.dumps(complete) + "\n")
                stream.write('{"type":"context.append_message","message":{"role":"assistant"')
            page = transcript.read_material(str(wire), cursor, session_id="ses_tail")
            self.assertEqual(page["status"], "ok")
            self.assertTrue(page["partial"])
            self.assertTrue(page["more"])
            self.assertIn("new complete work", page["text"])
            self.assertEqual(page["end"], cursor + len(json.dumps(complete)) + 1)

    def test_first_page_with_more_bytes_never_judges_unknown_format(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            garbage = [rec("llm.request", model="k", turnStep="1.%d" % i) for i in range(40)]
            _root, wire = write_session(
                home, "ses_big", garbage, state=dict(createdAt=1_000_000_000_000)
            )
            first = transcript.read_material(
                str(wire), 0, session_id="ses_big", max_scan_bytes=1024
            )
            self.assertEqual(first["status"], "ok", first.get("coverage_note"))
            self.assertTrue(first["more"])
            self.assertEqual(first["records"], 0)
            cursor = first["end"]
            while True:
                page = transcript.read_material(
                    str(wire), cursor, session_id="ses_big", max_scan_bytes=1024
                )
                self.assertNotEqual(page["status"], "unknown-format")
                if not page["more"]:
                    break
                self.assertGreater(page["end"], cursor)
                cursor = page["end"]

    def test_oversize_first_record_does_not_terminate_first_page(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            giant = rec("llm.request", model="k", data="x" * 4096)
            valid = rec(
                "context.append_message",
                message=dict(
                    role="user",
                    content=[dict(type="text", text="after the giant record")],
                    origin=dict(kind="user"),
                ),
            )
            _root, wire = write_session(
                home, "ses_giant", [giant, valid], state=dict(createdAt=1_000_000_000_000)
            )
            first = transcript.read_material(
                str(wire), 0, session_id="ses_giant", max_scan_bytes=1024
            )
            self.assertEqual(first["status"], "ok", first.get("coverage_note"))
            self.assertTrue(first["more"])
            seen = first["end"]
            while seen < first["snapshot_size"]:
                page = transcript.read_material(
                    str(wire), seen, session_id="ses_giant", max_scan_bytes=1024
                )
                if page["end"] == seen and not page["more"]:
                    break
                seen = page["end"]
                if "after the giant record" in page["text"]:
                    break
            final = transcript.read_material(str(wire), seen, session_id="ses_giant")
            self.assertNotEqual(final["status"], "unknown-format")

    def test_turn_label_uses_each_records_own_turn_id(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            records = [
                rec(
                    "context.append_loop_event",
                    event=dict(
                        type="tool.call", name="Bash", turnId=3, toolCallId="c1", args={}
                    ),
                ),
                rec(
                    "context.append_loop_event",
                    event=dict(
                        type="content.part", part=dict(type="text", text="next turn text"),
                        turnId=4,
                    ),
                ),
                rec(
                    "context.append_message",
                    message=dict(
                        role="assistant",
                        content=[dict(type="text", text="unlabeled message")],
                    ),
                ),
                rec(
                    "context.append_loop_event",
                    event=dict(type="tool.call", name="Bash", toolCallId="c2", args={}),
                ),
            ]
            _root, wire = write_session(home, "ses_turns", records)
            result = transcript.read_material(str(wire), 0, session_id="ses_turns")
            self.assertIn("turn=3", result["text"])
            self.assertIn("turn=4", result["text"])
            unlabeled = [
                line for line in result["text"].splitlines()
                if "unlabeled message" in line or "c2" in line
            ]
            labeled = [
                line for line in result["text"].splitlines()
                if line.startswith("[") and "turn=unknown" in line
            ]
            self.assertTrue(labeled, result["text"])
            self.assertNotIn("turn=3\n\n[assistant", result["text"])
            self.assertEqual(len(unlabeled), 2)
