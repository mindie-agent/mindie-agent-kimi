"""Native Kimi identity: hook payload, session index, one-shot nonce binds.

Kimi 0.42.0 MCP tools/call sends name/arguments only. PreToolUse has
session_id, tool_call_id, tool_name, tool_input. Slash commands submit
origin.kind=plugin_command and skip UserPromptSubmit (kind==user only).
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
    state_dir,
)

SESSION_RE = re.compile(IDENTITY)
NONCE_RE = re.compile(NONCE)
MAX_HOOK_BYTES = 128 * 1024
COMMANDS = {
    "init",
    "status",
    "deactivate",
    "sharing-enable",
    "sharing-disable",
    "sharing-status",
    "recover",
}


def require_session(value) -> str:
    if not isinstance(value, str) or not SESSION_RE.fullmatch(value):
        raise ValueError("native session identity is missing or invalid")
    return value


def require_nonce(value) -> str:
    if not isinstance(value, str) or not NONCE_RE.fullmatch(value):
        raise ValueError("request_nonce is missing or invalid")
    return value


def parse_hook(raw: bytes) -> dict:
    if len(raw) > MAX_HOOK_BYTES:
        raise ValueError("hook input exceeds limit")
    event = json.loads(raw)
    if not isinstance(event, dict):
        raise ValueError("hook payload must be one JSON object")
    return event


def session_from_hook(event: dict) -> str:
    return require_session(event.get("session_id"))


def is_mindie_mcp_tool(name) -> bool:
    return isinstance(name, str) and name.startswith(MCP_QUALIFIED_PREFIX)


def local_tool_name(name: str) -> str:
    if "__" in name:
        return name.rsplit("__", 1)[-1]
    return name


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
    if index.is_file():
        found = _from_index(index, session_id, home)
        if found is not None:
            return found
    sessions = home / "sessions"
    if sessions.is_dir():
        hits = []
        for bucket in sessions.iterdir():
            if not bucket.is_dir():
                continue
            candidate = bucket / session_id
            if candidate.is_dir():
                hits.append(candidate.resolve())
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ValueError("session id matched more than one directory")
    raise ValueError("native session directory was not found for this session id")


def locate_main_wire(session_id: str, *, kimi_home=None) -> Path:
    root = locate_session_dir(session_id, kimi_home=kimi_home)
    wire = root / "agents" / "main" / "wire.jsonl"
    if not wire.is_file():
        raise ValueError("main agent wire.jsonl is missing for this session")
    return wire.resolve()


def session_cwd(session_id: str, *, kimi_home=None) -> str:
    state = session_state(session_id, kimi_home=kimi_home)
    cwd = state.get("cwd")
    if not isinstance(cwd, str) or not os.path.isabs(cwd):
        raise ValueError("native session cwd is unavailable")
    return cwd


def session_state(session_id: str, *, kimi_home=None) -> dict:
    root = locate_session_dir(session_id, kimi_home=kimi_home)
    path = root / "state.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _from_index(index: Path, session_id: str, home: Path) -> Path | None:
    try:
        raw = index.read_bytes()
    except OSError:
        return None
    if len(raw) > 8 * 1024 * 1024:
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
        ident = row.get("sessionId") or row.get("session_id") or row.get("id")
        if ident != session_id:
            continue
        directory = row.get("sessionDir") or row.get("session_dir")
        if isinstance(directory, str) and directory:
            path = Path(directory)
            if not path.is_absolute():
                path = home / path
            if path.is_dir():
                resolved = path.resolve()
                if matched is not None and matched != resolved:
                    raise ValueError("session index has contradictory directories for this id")
                matched = resolved
    return matched


def _tail_records(session_id: str, *, kimi_home=None):
    wire = locate_main_wire(session_id, kimi_home=kimi_home)
    try:
        size = wire.stat().st_size
        with wire.open("rb") as stream:
            if size > 256 * 1024:
                stream.seek(size - 256 * 1024)
                stream.readline()
            raw = stream.read(256 * 1024)
    except OSError:
        return []
    records = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _opening_origin(record):
    """Turn-opening native origin, or None if this record does not open a turn."""
    if record.get("type") == "turn.prompt":
        origin = record.get("origin")
        return origin if isinstance(origin, dict) else {}
    if record.get("type") == "context.append_message":
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "user":
            return None
        origin = message.get("origin")
        if not isinstance(origin, dict):
            return {}
        if origin.get("kind") == "injection":
            return None
        return origin
    return None


def current_turn_origin(session_id: str, *, kimi_home=None) -> dict:
    """CURRENT turn-opening origin only. Never the last matching MindIE command."""
    for record in reversed(_tail_records(session_id, kimi_home=kimi_home)):
        origin = _opening_origin(record)
        if origin is None:
            continue
        return origin
    raise ValueError("current native turn origin is unavailable")


def require_current_plugin_command(session_id: str, command: str, *, kimi_home=None) -> dict:
    origin = current_turn_origin(session_id, kimi_home=kimi_home)
    if origin.get("kind") != "plugin_command":
        raise ValueError("current turn is not a native MindIE slash command")
    if origin.get("pluginId") != PLUGIN_ID:
        raise ValueError("current turn is not this plugin's slash command")
    name = origin.get("commandName")
    if name not in COMMANDS:
        raise ValueError("current slash command is not a MindIE entry")
    if name != command:
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


def bind_db_path(config=None) -> Path:
    return state_dir(config) / "mcp-binds.sqlite3"


def current_turn_identity(session_id: str, *, kimi_home=None) -> str:
    """CURRENT latest turn.prompt promptId from this task's own wire.

    No persisted turn store: default-off tasks must create no state.
    """
    session_id = require_session(session_id)
    for record in reversed(_tail_records(session_id, kimi_home=kimi_home)):
        if record.get("type") != "turn.prompt":
            continue
        prompt_id = record.get("promptId")
        if isinstance(prompt_id, str) and prompt_id:
            return prompt_id[:256]
    raise ValueError("current native turn identity is unavailable")


def _connect(path: Path, *, create: bool):
    if not path.exists() and not create:
        raise FileNotFoundError(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=0.4)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
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
    """PreToolUse: bind nonce to exact tool + arguments + native tool_call_id."""
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
                "SELECT session, tool, args_digest, tool_call_id, call_digest, status "
                "FROM binds WHERE nonce=?",
                (nonce,),
            ).fetchone()
            if row is None:
                db.execute(
                    "INSERT INTO binds VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        nonce,
                        session_id,
                        tool_name,
                        local,
                        adigest,
                        digest,
                        tool_call_id,
                        "bound",
                        now,
                        now,
                    ),
                )
                return "bound"
            other, tool, stored_args, stored_call, stored_digest, status = row
            same = (
                other == session_id
                and tool == tool_name
                and stored_args == adigest
                and stored_call == tool_call_id
                and stored_digest == digest
            )
            if not same:
                db.execute(
                    "UPDATE binds SET status='ambiguous', updated=? WHERE nonce=?",
                    (now, nonce),
                )
                return "ambiguous"
            return status
    finally:
        db.close()


def claim_nonce(nonce: str, tool_name: str, arguments, *, config=None) -> str:
    """MCP: consume a matching PreToolUse bind. Persist the attempt first."""
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
                "SELECT session, tool, local_tool, args_digest, status, created "
                "FROM binds WHERE nonce=?",
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
            db.execute(
                "UPDATE binds SET status='claimed', updated=? WHERE nonce=?",
                (now, nonce),
            )
            return session
    finally:
        db.close()


def finish_nonce(nonce: str, succeeded: bool, *, config=None) -> None:
    nonce = require_nonce(nonce)
    path = bind_db_path(config)
    if not path.is_file():
        return
    db = _connect(path, create=False)
    try:
        with db:
            db.execute(
                "UPDATE binds SET status=?, updated=? WHERE nonce=? AND status='claimed'",
                ("succeeded" if succeeded else "failed", time.time(), nonce),
            )
    except sqlite3.Error:
        pass
    finally:
        db.close()
