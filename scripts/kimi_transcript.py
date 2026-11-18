"""Kimi Code `wire.jsonl` public-record transcript parser (`FileIdentity`, `identify`, `to_seconds`,
`read_material`) for the shared knowledge capture pipeline.

[Role] Bounded pages of Kimi's native session transcript: file identity, session ownership,
fork boundaries (`state.json` `forkedFrom` / `createdAt`), activation boundaries.
[Invariants] Unix-ms timestamps (`>= MS_THRESHOLD = 1e12`) become seconds; only
`origin.kind == "user"` user messages and non-thinking assistant `text` parts are exported;
forked sessions exclude records before the fork's `createdAt`; `[start, end)` accounting digests
exactly the consumed raw bytes with SHA-256; malformed/truncated tails are reported (`partial` /
`discarded_records`) and never silently consumed.
[Timeouts & Locks] Bounded by `max_scan_bytes` (`16 MiB`), `max_seconds` (`2.0s`),
`max_text_bytes` (`48 KiB`), and `MAX_RECORDS` (`200`); zero locks.
[Side-Effect Policy] Purely read-only over `wire.jsonl` and the parent `state.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

MAX_RECORDS = 200
ANCHOR_BYTES = 512
MS_THRESHOLD = 1e12
USER_ORIGINS = {"user"}

@dataclass(frozen=True)
class FileIdentity:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    anchor_len: int = 0
    anchor_digest: str = ""

    @property
    def key(self) -> str:
        return f"{self.path}|{self.dev}:{self.ino}"

    def serialize(self) -> str:
        return json.dumps(dict(dev=self.dev, ino=self.ino, anchor_len=self.anchor_len,
                               anchor_digest=self.anchor_digest), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def unserialize(text, path):
        try:
            data = json.loads(text)
            anchor_len, anchor_digest = data["anchor_len"], data["anchor_digest"]
        except (ValueError, KeyError, TypeError):
            return None
        if type(anchor_len) is not int or not 0 < anchor_len <= ANCHOR_BYTES:
            return None
        if not isinstance(anchor_digest, str) or len(anchor_digest) != 64:
            return None
        return FileIdentity(path, int(data["dev"]), int(data["ino"]), 0, 0, anchor_len, anchor_digest)

def identify(path) -> FileIdentity | None:
    try:
        with open(path, "rb") as stream:
            st, anchor = os.fstat(stream.fileno()), stream.read(ANCHOR_BYTES)
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return FileIdentity(str(Path(path).resolve()), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns,
                        len(anchor), hashlib.sha256(anchor).hexdigest())

def to_seconds(raw):
    """Convert Unix milliseconds (`>= MS_THRESHOLD`) or seconds to seconds."""
    if type(raw) not in (int, float) or raw <= 0:
        return None
    return float(raw) / 1000.0 if raw >= MS_THRESHOLD else float(raw)

def _session_in_path(path) -> str | None:
    parts = Path(path).resolve().parts
    if "sessions" not in parts:
        return None
    index = parts.index("sessions")
    return parts[index + 2] if index + 2 < len(parts) else None

def _timestamp(record):
    return to_seconds(record.get("time")) or to_seconds(record.get("created_at"))

def _text_parts(content):
    if not isinstance(content, list):
        return ""
    return "\n".join(item["text"] for item in content if isinstance(item, dict)
                     and item.get("type") == "text" and isinstance(item.get("text"), str))

def _origin_kind(message):
    origin = message.get("origin") if isinstance(message, dict) else None
    return origin.get("kind") if isinstance(origin, dict) and isinstance(origin.get("kind"), str) else None

def _extract(record):
    """Return `(kind, text)` for public user/assistant text, else `None`."""
    kind = record.get("type")
    if kind == "context.append_message":
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}:
            return None
        if message.get("role") == "user" and _origin_kind(message) not in USER_ORIGINS:
            return None
        text = _text_parts(message.get("content"))
        return (message["role"], text) if text.strip() else None
    if kind == "context.append_loop_event":
        event = record.get("event")
        part = event.get("part") if isinstance(event, dict) and event.get("type") == "content.part" else None
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
            text = part["text"].strip()
            return ("assistant", text) if text else None
    return None

def _fork_boundary(path) -> float | None:
    try:
        data = json.loads((Path(path).resolve().parents[2] / "state.json").read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError, IndexError):
        raise OSError("native session state unavailable; inherited material not read") from None
    if not isinstance(data, dict):
        raise OSError("native session state invalid; inherited material not read")
    if not (data.get("forkedFrom") or data.get("forked_from")):
        return None
    created = to_seconds(data.get("createdAt") or data.get("created_at"))
    if created is None:
        raise ValueError("forked session lacks state.createdAt; inherited material not read")
    return created

def read_material(path, start, *, session_id=None, not_before=None, expected=None,
                  max_scan_bytes=16777216, max_seconds=2.0, max_text_bytes=49152, scan_until=None):
    """Read one `[start, end)` public-material page from `wire.jsonl`."""
    if type(start) is not int or start < 0:
        raise ValueError("start must be a nonnegative offset")
    if max_scan_bytes <= 0 or max_seconds <= 0 or max_text_bytes <= 0:
        raise ValueError("invalid scan budget")
    if scan_until is not None and (type(scan_until) is not int or scan_until < start):
        raise ValueError("scan_until must be an exact byte boundary at or after start")
    boundary = to_seconds(not_before) if not_before is not None else None
    result = dict(status="ok", start=start, end=start, digest=hashlib.sha256(b"").hexdigest(), text="",
                  records=0, skipped_records=0, oversize_records=0, partial=False, more=False,
                  timestamps_reliable=True, session_match=None, coverage_note=None, coverage=[])
    consumed, included, text_size, begun = hashlib.sha256(), [], 0, time.monotonic()
    try:
        fork_time = _fork_boundary(path)
    except ValueError as exc:
        result.update(status="unknown-format", coverage_note=str(exc), text="")
        return result
    try:
        with open(path, "rb") as stream:
            st = os.fstat(stream.fileno())
            anchor = stream.read(ANCHOR_BYTES)
            current = FileIdentity(str(Path(path).resolve()), st.st_dev, st.st_ino, st.st_size,
                                   st.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())
            result["identity"], result["snapshot_size"] = current.serialize(), st.st_size
            if expected is not None:
                stream.seek(0)
                if (expected.path != current.path or expected.dev != st.st_dev or expected.ino != st.st_ino
                        or not expected.anchor_len
                        or hashlib.sha256(stream.read(expected.anchor_len)).hexdigest() != expected.anchor_digest):
                    result.update(status="replaced", coverage_note="transcript changed before read")
                    return result
            if st.st_size < start:
                result.update(status="replaced", coverage_note="transcript truncated")
                return result
            owner = _session_in_path(path)
            if owner:
                result["session_match"] = not session_id or owner == session_id
                if session_id and owner != session_id:
                    result.update(status="wrong-task", coverage_note="transcript belongs to another task")
                    return result
            elif session_id:
                result.update(status="unknown-format", coverage_note="task path identity unavailable; no public read")
                return result
            stream.seek(max(0, start - 1))
            if start > 0 and stream.read(1) != b"\n":
                result.update(status="invalid-boundary", coverage_note="cursor is not a whole-record boundary")
                return result
            stream.seek(start)
            effective_size = min(st.st_size, scan_until) if scan_until is not None else st.st_size
            end_limit = min(effective_size, start + max_scan_bytes)
            while stream.tell() < end_limit and time.monotonic() - begun < max_seconds:
                offset = stream.tell()
                raw = stream.readline(min(end_limit - offset, max_text_bytes * 4 + 65536))
                if not raw:
                    break
                if not raw.endswith(b"\n"):
                    result["partial"] = offset + len(raw) == st.st_size
                    break
                try:
                    record = json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    record = None
                if not isinstance(record, dict):
                    result.setdefault("discarded_records", []).append(
                        dict(start=offset, end=stream.tell(), reason="invalid record"))
                    consumed.update(raw)
                    result["end"], result["skipped_records"] = stream.tell(), result["skipped_records"] + 1
                    continue
                stamp, extracted = _timestamp(record), _extract(record)
                if fork_time is not None and (stamp is None or stamp < fork_time):
                    extracted = None
                if extracted and extracted[1]:
                    if boundary is not None and stamp is None:
                        result.setdefault("discarded_records", []).append(
                            dict(start=offset, end=stream.tell(), reason="missing public timestamp"))
                        consumed.update(raw)
                        result["end"], result["skipped_records"] = stream.tell(), result["skipped_records"] + 1
                        continue
                    if boundary is not None and stamp < boundary:
                        extracted = None
                    else:
                        text = extracted[1].replace("\r\n", "\n").replace("\r", "\n")
                        rendered = f"### {extracted[0]}\n{text}"
                        size = len(rendered.encode()) + 2
                        if size > max_text_bytes:
                            if included:
                                break
                            result["oversize_records"] += 1
                            extracted = None
                        elif text_size + size > max_text_bytes or len(included) >= MAX_RECORDS:
                            break
                        else:
                            included.append(rendered)
                            text_size += size
                if not extracted or not extracted[1]:
                    result["skipped_records"] += 1
                consumed.update(raw)
                result["end"] = stream.tell()
            result["more"] = result["end"] < effective_size
    except TimeoutError:
        raise
    except OSError:
        result.update(status="missing", coverage_note="transcript unreadable")
        return result
    result.update(digest=consumed.hexdigest(), text="\n\n".join(included), records=len(included))
    if result["status"] == "ok" and result["end"] == start and not result["partial"]:
        result["status"] = "unchanged"
    return result
