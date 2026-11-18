"""Kimi `wire.jsonl` public-record parsing, fork boundaries, and byte accounting."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]


def _transcript():
    if str(REPO / "scripts") not in sys.path:
        sys.path.insert(0, str(REPO / "scripts"))
    spec = importlib.util.spec_from_file_location("kimi_transcript_test", REPO / "scripts/kimi_transcript.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_session(root: Path, records: list[dict], state: dict | None = None, session: str = "ses_test") -> Path:
    base = root / "sessions" / "workdir" / session
    wire = base / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    (base / "state.json").write_text(json.dumps(state or {"createdAt": 1000}), encoding="utf-8")
    return wire


def _record(role: str, text: str, stamp: int, *, origin: str | None = "user") -> dict:
    message = {"role": role, "content": [{"type": "text", "text": text}, {"type": "think", "think": "hidden-reasoning"}]}
    if origin is not None:
        message["origin"] = {"kind": origin}
    return {"type": "context.append_message", "time": stamp, "message": message}


def test_to_seconds_handles_unix_milliseconds() -> None:
    transcript = _transcript()
    assert transcript.to_seconds(2_000_000_000_000) == 2_000_000_000.0
    assert transcript.to_seconds(1.5) == 1.5
    assert transcript.to_seconds(None) is None
    assert transcript.to_seconds(0) is None
    assert transcript.to_seconds("1") is None


def test_file_identity_roundtrip(tmp_path: Path) -> None:
    transcript = _transcript()
    wire = _write_session(tmp_path, [_record("user", "hello", 2_000_000_000_000)])
    identity = transcript.identify(wire)
    assert identity is not None and identity.path == str(wire.resolve())
    assert identity.size == wire.stat().st_size and identity.anchor_len > 0
    restored = transcript.FileIdentity.unserialize(identity.serialize(), identity.path)
    assert restored is not None and restored.anchor_digest == identity.anchor_digest
    assert transcript.FileIdentity.unserialize("{}", identity.path) is None


def test_public_material_filters_origins_and_thinking(tmp_path: Path) -> None:
    transcript = _transcript()
    records = [
        _record("user", "public user question", 2_000_000_000_000),
        _record("user", "injected system text", 2_000_000_000_001, origin="system"),
        _record("user", "legacy no origin", 2_000_000_000_002, origin=None),
        _record("assistant", "public assistant answer", 2_000_000_000_003),
        {"type": "context.append_loop_event", "time": 2_000_000_000_004,
         "event": {"type": "content.part", "part": {"type": "text", "text": "loop assistant text"}}},
        {"type": "context.append_loop_event", "time": 2_000_000_000_005,
         "event": {"type": "content.part", "part": {"type": "think", "think": "hidden"}}},
        {"type": "turn.prompt", "time": 2_000_000_000_006},
    ]
    wire = _write_session(tmp_path, records)
    result = transcript.read_material(wire, 0, session_id="ses_test")
    assert result["status"] == "ok"
    assert "public user question" in result["text"]
    assert "public assistant answer" in result["text"]
    assert "loop assistant text" in result["text"]
    assert "injected system text" not in result["text"]
    assert "legacy no origin" not in result["text"]
    assert "hidden" not in result["text"]
    assert result["records"] == 3
    payload = wire.read_bytes()
    assert result["end"] == len(payload)
    assert result["digest"] == hashlib.sha256(payload).hexdigest()
    assert result["timestamps_reliable"] is True


def test_fork_boundary_excludes_inherited_material(tmp_path: Path) -> None:
    transcript = _transcript()
    records = [
        _record("user", "inherited before fork", 2_000_000_000_000),
        _record("user", "after fork", 2_000_000_000_200),
    ]
    wire = _write_session(tmp_path, records, {"forkedFrom": "ses_source", "createdAt": 2_000_000_000_150})
    result = transcript.read_material(wire, 0, session_id="ses_test")
    assert "after fork" in result["text"]
    assert "inherited before fork" not in result["text"]


def test_byte_accounting_and_unchanged_tail(tmp_path: Path) -> None:
    transcript = _transcript()
    wire = _write_session(tmp_path, [_record("user", "one", 2_000_000_000_000), _record("assistant", "two", 2_000_000_000_001)])
    first = transcript.read_material(wire, 0, session_id="ses_test")
    expected = hashlib.sha256(wire.read_bytes()[: first["end"]]).hexdigest()
    assert first["digest"] == expected
    assert first["more"] is False
    tail = transcript.read_material(wire, first["end"], session_id="ses_test")
    assert tail["status"] == "unchanged" and tail["end"] == first["end"]
    assert tail["digest"] == hashlib.sha256(b"").hexdigest()
    first_line_end = len(wire.read_bytes().splitlines(keepends=True)[0])
    snap = transcript.read_material(wire, 0, session_id="ses_test", scan_until=first_line_end)
    assert snap["end"] == first_line_end and snap["more"] is False
