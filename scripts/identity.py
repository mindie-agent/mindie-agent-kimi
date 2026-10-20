"""Native Kimi session identity, turn-origin verification, and one-shot nonce binds.

[Role]
Locates Kimi session directories (`session_index.jsonl` / `sessions/`) and main
agent `wire.jsonl`, verifies turn-opening user/slash/skill origins (`require_current_entry`),
and manages one-shot SQLite nonce bindings (`publish_nonce_bind`, `claim_nonce`,
`finish_nonce`) between `PreToolUse` and MCP `tools/call`.

[Invariants]
- Kimi 0.42+ MCP `tools/call` carries `name` and `arguments` only; session
  identity is transferred exclusively via `PreToolUse` -> `mcp-binds.sqlite3`.
- Nonce binds expire after `120s`, reject replays (`claimed`/`succeeded`/`failed`),
  and mark conflicting binds `ambiguous`.
- Turn-origin verification filters out inherited fork history (`< state.createdAt`),
  injected messages, foreign skills, and model-initiated skill use.

[Timeouts & Locks]
- `mcp-binds.sqlite3` uses `timeout=5.0`, `PRAGMA busy_timeout=5000`, and
  `BEGIN IMMEDIATE` transactions.
- Backward tail scan of `wire.jsonl` starts at `256 KiB` and caps at `8 MiB`.

[Side-Effect Policy]
- Reads `session_index.jsonl`, `state.json`, and `wire.jsonl`; mutates only
  `<state_dir>/mcp-binds.sqlite3`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from paths import (
    IDENTITY,
    MCP_QUALIFIED_PREFIX,
    NONCE,
    PLUGIN_ID,
    kimi_home_from_env,
    plugin_root_from_env,
    state_dir,
)

SESSION_RE = re.compile(IDENTITY)
NONCE_RE = re.compile(NONCE)
COMMANDS = {
    "mindie-agent",
    "init",
    "status",
    "deactivate",
    "sharing-enable",
    "sharing-disable",
    "sharing-status",
    "recover",
    "reporting-status",
}
ENTRY_ALIASES = {"mindie-agent": "init"}
TAIL_CHUNK = 256 * 1024
TAIL_MAX = 8 * 1024 * 1024
MS_THRESHOLD = 1e12
_NESTED_AMBIGUOUS = "nested_activation_ambiguous"
_NESTED_STEERING = "nested_activation_steering"
ENTRY_TEXT_RE = re.compile(r"/" + re.escape(PLUGIN_ID) + r"(?=\s|$)")


def require_session(value) -> str:
    if not isinstance(value, str) or not SESSION_RE.fullmatch(value):
        raise ValueError("native session identity is missing or invalid")
    return value


def require_nonce(value) -> str:
    if not isinstance(value, str) or not NONCE_RE.fullmatch(value):
        raise ValueError("request_nonce is missing or invalid")
    return value


def parse_hook(raw: bytes) -> dict:
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook payload must be one JSON object")
    return event


def session_from_hook(event: dict) -> str:
    return require_session(event.get("session_id"))


def is_mindie_mcp_tool(name) -> bool:
    return isinstance(name, str) and name.startswith(MCP_QUALIFIED_PREFIX)


def local_tool_name(name: str) -> str:
    return name.rsplit("__", 1)[-1] if "__" in name else name


def canonical_args(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def bind_digest(tool_name, arguments, tool_call_id) -> str:
    payload = dict(
        tool=tool_name,
        arguments=arguments if isinstance(arguments, dict) else {},
        tool_call_id=tool_call_id or "",
    )
    return hashlib.sha256(canonical_args(payload).encode("utf-8")).hexdigest()


def args_digest(arguments) -> str:
    return hashlib.sha256(
        canonical_args(arguments if isinstance(arguments, dict) else {}).encode("utf-8")
    ).hexdigest()


def locate_session_dir(session_id: str, *, kimi_home=None) -> Path:
    session_id = require_session(session_id)
    home = Path(kimi_home) if kimi_home is not None else kimi_home_from_env()
    if home is None:
        raise ValueError("KIMI_CODE_HOME is required to locate native session records")
    index = home / "session_index.jsonl"
    if index.is_file() and (found := _from_index(index, session_id, home)) is not None:
        return found
    sessions = home / "sessions"
    if sessions.is_dir():
        hits = [
            (bucket / session_id).resolve()
            for bucket in sessions.iterdir()
            if bucket.is_dir() and (bucket / session_id).is_dir()
        ]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError("session id matched more than one directory")
    raise ValueError("native session directory was not found for this session id")


def locate_main_wire(session_id: str, *, kimi_home=None) -> Path:
    wire = locate_session_dir(session_id, kimi_home=kimi_home) / "agents" / "main" / "wire.jsonl"
    if not wire.is_file():
        raise ValueError("main agent wire.jsonl is missing for this session")
    return wire.resolve()


def session_cwd(session_id: str, *, kimi_home=None) -> str:
    cwd = session_state(session_id, kimi_home=kimi_home).get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError("native session cwd is unavailable")
    return cwd


def session_state(session_id: str, *, kimi_home=None) -> dict:
    path = locate_session_dir(session_id, kimi_home=kimi_home) / "state.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_index(index: Path, session_id: str, home: Path) -> Path | None:
    try:
        raw = index.read_bytes()
    except OSError:
        return None
    matched = None
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if (row.get("sessionId") or row.get("session_id") or row.get("id")) != session_id:
            continue
        directory = row.get("sessionDir") or row.get("session_dir")
        if isinstance(directory, str) and directory:
            path = Path(directory) if Path(directory).is_absolute() else home / directory
            if path.is_dir():
                resolved = path.resolve()
                if matched is not None and matched != resolved:
                    raise ValueError("session index has contradictory directories for this id")
                matched = resolved
    return matched


def _to_seconds(raw):
    if type(raw) not in (int, float) or raw <= 0:
        return None
    value = float(raw)
    return value / 1000.0 if value >= MS_THRESHOLD else value


def _opens_turn(record):
    origin = _opening_origin(record)
    if origin is None or record.get("type") == "turn.steer":
        return False
    return not (
        origin.get("kind") == "skill_activation"
        and (origin.get("inTurn") is True or origin.get("trigger") == "model-tool")
    )


def _tail_records(session_id: str, *, kimi_home=None):
    wire = locate_main_wire(session_id, kimi_home=kimi_home)
    try:
        size = wire.stat().st_size
        window = TAIL_CHUNK
        while True:
            with wire.open("rb") as stream:
                if size > window:
                    stream.seek(size - window)
                    stream.readline()
                raw = stream.read(window)
            records = []
            for line in raw.splitlines():
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    records.append(record)
            if window >= size or window >= TAIL_MAX or any(_opens_turn(r) for r in records):
                return records
            window = min(window * 4, TAIL_MAX, size)
    except OSError:
        return []


def _normalize_activation(origin, *, steering):
    if origin.get("kind") != "user" or "skillActivations" not in origin:
        return origin
    activations = origin.get("skillActivations")
    if not isinstance(activations, list):
        return {"kind": _NESTED_AMBIGUOUS}
    mine = [item for item in activations if isinstance(item, dict) and item.get("skillName") == PLUGIN_ID]
    if not mine:
        return origin
    if len(mine) != 1:
        return {"kind": _NESTED_AMBIGUOUS}
    if steering:
        return {"kind": _NESTED_STEERING}
    activation = mine[0]
    return {
        "kind": "skill_activation",
        "activationId": activation.get("activationId"),
        "skillName": PLUGIN_ID,
        "skillPath": activation.get("skillPath"),
        "skillArgs": activation.get("skillArgs"),
    }


def _opening_origin(record):
    rtype = record.get("type")
    if rtype in {"turn.prompt", "turn.steer"}:
        origin = record.get("origin")
        return _normalize_activation(origin if isinstance(origin, dict) else {}, steering=rtype == "turn.steer")
    if rtype == "context.append_message":
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            return None
        origin = message.get("origin")
        if not isinstance(origin, dict):
            return {}
        if origin.get("kind") == "injection":
            return None
        return _normalize_activation(origin, steering=False)
    return None


def _turn_openings(session_id, *, kimi_home=None):
    records = _tail_records(session_id, kimi_home=kimi_home)
    boundary = None
    state = session_state(session_id, kimi_home=kimi_home)
    if state.get("forkedFrom") or state.get("forked_from"):
        boundary = _to_seconds(state.get("createdAt") or state.get("created_at"))
        if boundary is None:
            raise ValueError("forked session lacks state.createdAt; inherited material is not trusted")
    pairs = []
    for record in records:
        origin = _opening_origin(record)
        if origin is None:
            continue
        if boundary is not None:
            stamp = _to_seconds(record.get("time") or record.get("created_at"))
            if stamp is None or stamp < boundary:
                continue
        pairs.append((record, origin))
    return pairs


def current_entry_scan(session_id: str, *, kimi_home=None):
    pairs = _turn_openings(session_id, kimi_home=kimi_home)
    if not pairs:
        raise ValueError("current native turn origin is unavailable")
    record, origin = pairs[-1]
    text = _record_text(record) if origin.get("kind") == "user" else ""
    return pairs, origin, text


def current_turn_origin(session_id: str, *, kimi_home=None) -> dict:
    _pairs, origin, _text = current_entry_scan(session_id, kimi_home=kimi_home)
    return origin


def current_user_text(session_id: str, *, kimi_home=None) -> str:
    _pairs, _origin, text = current_entry_scan(session_id, kimi_home=kimi_home)
    return text


def _validate_plugin_command_origin(origin: dict, command: str) -> dict:
    if origin.get("pluginId") != PLUGIN_ID:
        raise ValueError("current turn is not this plugin's slash command")
    name = origin.get("commandName")
    if name not in COMMANDS:
        raise ValueError("current slash command is not a MindIE entry")
    if ENTRY_ALIASES.get(name, name) != command:
        raise ValueError("current slash command does not match this operation")
    activation_id = origin.get("activationId")
    if not isinstance(activation_id, str) or not activation_id:
        raise ValueError("native plugin command lacks activationId")
    args = origin.get("commandArgs")
    return dict(
        command=command,
        arguments=args if isinstance(args, str) else "",
        activation_id=activation_id,
    )


def require_current_plugin_command(session_id: str, command: str, *, kimi_home=None) -> dict:
    origin = current_turn_origin(session_id, kimi_home=kimi_home)
    if origin.get("kind") != "plugin_command":
        raise ValueError("current turn is not a native MindIE slash command")
    return _validate_plugin_command_origin(origin, command)


def _record_text(record):
    items = (
        (record.get("message") or {}).get("content")
        if record.get("type") == "context.append_message"
        else record.get("input")
    )
    for item in items or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            return item["text"]
    return ""


def _invokes_entry(text) -> bool:
    return isinstance(text, str) and bool(ENTRY_TEXT_RE.match(text.lstrip()))


def _current_skill_activation(origin, command):
    if origin.get("skillName") != PLUGIN_ID:
        raise ValueError("current skill activation is not the MindIE entry")
    skill_path = origin.get("skillPath")
    if not isinstance(skill_path, str) or not skill_path:
        raise ValueError("skill activation lacks the native skill path")
    expected = (plugin_root_from_env() / "skills" / PLUGIN_ID / "SKILL.md").resolve()
    try:
        actual = Path(skill_path).resolve()
    except (OSError, ValueError):
        raise ValueError("skill activation path is not usable") from None
    if actual != expected:
        raise ValueError("skill activation is not this plugin's mindie-agent skill")
    trigger = origin.get("trigger")
    if trigger is not None and trigger not in {"user-slash", "model-tool"}:
        raise ValueError("skill activation trigger is not a user entry")
    activation_id = origin.get("activationId")
    if not isinstance(activation_id, str) or not activation_id:
        raise ValueError("native skill activation lacks activationId")
    args = origin.get("skillArgs")
    return dict(
        command=command,
        arguments=args if isinstance(args, str) else "",
        activation_id=activation_id,
    )


def _entry_from_openings(openings, command: str) -> dict:
    _record, origin = openings[-1]
    kind = origin.get("kind")
    if kind == _NESTED_AMBIGUOUS:
        raise ValueError("current turn has ambiguous MindIE skill activations")
    if kind == _NESTED_STEERING:
        raise ValueError("an in-turn nested skill activation is not the user entry")
    if kind == "plugin_command":
        return _validate_plugin_command_origin(origin, command)
    if kind == "skill_activation":
        if command != "init":
            raise ValueError("skill activation only performs the entry operation")
        entry = _current_skill_activation(origin, command)
        if origin.get("trigger") == "model-tool" or origin.get("inTurn") is True:
            opener_text = next(
                (_record_text(rec) for rec, org in reversed(openings) if org.get("kind") == "user"),
                "",
            )
            if not _invokes_entry(opener_text):
                raise ValueError("model-initiated skill use is not the user entry")
        return entry
    raise ValueError("current turn is not the native MindIE entry")


def require_current_entry(session_id: str, command: str = "init", *, kimi_home=None) -> dict:
    openings = _turn_openings(session_id, kimi_home=kimi_home)
    if not openings:
        raise ValueError("current native turn origin is unavailable")
    return _entry_from_openings(openings, command)


def bind_db_path(config=None) -> Path:
    return state_dir(config) / "mcp-binds.sqlite3"


def _connect(path: Path, *, create: bool):
    if not path.exists() and not create:
        raise FileNotFoundError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=5.0)
    db.execute("PRAGMA busy_timeout=5000;")
    if create:
        db.execute("PRAGMA journal_mode=WAL;")
    return db


def _ensure_binds(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS binds("
        "nonce TEXT PRIMARY KEY, session TEXT NOT NULL, tool TEXT NOT NULL, "
        "local_tool TEXT NOT NULL, args_digest TEXT NOT NULL, call_digest TEXT NOT NULL, "
        "tool_call_id TEXT NOT NULL, status TEXT NOT NULL, "
        "created REAL NOT NULL, updated REAL NOT NULL)"
    )


def publish_nonce_bind(
    session_id: str,
    nonce: str,
    tool_name: str,
    tool_call_id,
    arguments,
    *,
    config=None,
):
    session_id = require_session(session_id)
    nonce = require_nonce(nonce)
    if not isinstance(tool_name, str) or not tool_name or len(tool_name) > 256:
        raise ValueError("invalid tool name")
    if not isinstance(tool_call_id, str) or not 0 < len(tool_call_id) <= 256:
        raise ValueError("native tool_call_id is required")
    if not isinstance(arguments, dict):
        raise ValueError("tool_input must be an object")
    digest = bind_digest(tool_name, arguments, tool_call_id)
    adigest = args_digest(arguments)
    local = local_tool_name(tool_name)
    now = time.time()
    db = _connect(bind_db_path(config), create=True)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _ensure_binds(db)
            row = db.execute(
                "SELECT session, tool, args_digest, tool_call_id, call_digest, status FROM binds WHERE nonce=?",
                (nonce,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO binds VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (nonce, session_id, tool_name, local, adigest, digest, tool_call_id, "bound", now, now),
                )
                return "bound"
            other, tool, stored_args, stored_call, stored_digest, status = row
            if (other, tool, stored_args, stored_call, stored_digest) != (
                session_id, tool_name, adigest, tool_call_id, digest
            ):
                db.execute("UPDATE binds SET status='ambiguous', updated=? WHERE nonce=?", (now, nonce))
                return "ambiguous"
            return status
    finally:
        db.close()


def claim_nonce(nonce: str, tool_name: str, arguments, *, config=None) -> str:
    nonce = require_nonce(nonce)
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError("invalid tool name")
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    now = time.time()
    path = bind_db_path(config)
    if not path.is_file():
        raise ValueError(
            "no native PreToolUse bind exists for this request_nonce; "
            "the host did not authorize this call"
        )
    adigest = args_digest(arguments)
    local = local_tool_name(tool_name)
    db = _connect(path, create=False)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            _ensure_binds(db)
            row = db.execute(
                "SELECT session, tool, local_tool, args_digest, status, created FROM binds WHERE nonce=?",
                (nonce,),
            ).fetchone()
            if row is None:
                raise ValueError("request_nonce was not bound by a native PreToolUse hook")
            session, tool, bound_local, stored_args, status, created = row
            if status == "ambiguous":
                raise ValueError("request_nonce is bound to contradictory native calls")
            if now - created > 120:
                raise ValueError("request_nonce bind expired")
            if status in {"claimed", "succeeded", "failed"}:
                raise ValueError("request_nonce has already been used; replay rejected")
            if status != "bound":
                raise ValueError("request_nonce is not authorized")
            tool_ok = tool == tool_name or bound_local == local or tool.endswith("__" + local)
            if not tool_ok or stored_args != adigest:
                raise ValueError("request_nonce does not match this tool and arguments")
            db.execute("UPDATE binds SET status='claimed', updated=? WHERE nonce=?", (now, nonce))
            return session
    finally:
        db.close()


def finish_nonce(nonce: str, succeeded: bool, *, config=None) -> None:
    """Persist the claimant's result; storage failures remain visible to MCP."""
    nonce = require_nonce(nonce)
    path = bind_db_path(config)
    db = _connect(path, create=False)
    try:
        with db:
            db.execute(
                "UPDATE binds SET status=?, updated=? WHERE nonce=? AND status='claimed'",
                ("succeeded" if succeeded else "failed", time.time(), nonce),
            )
    finally:
        db.close()
