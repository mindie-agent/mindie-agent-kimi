#!/usr/bin/env python3
"""[Role] Thin Kimi runtime adapter: resolves the `kimi` `ProfileRef`, serves `mcp <surface>`,
and executes `hook <event>` over `mindie_harness` (`API_VERSION = 1`).
[Invariants] `knowledge`, `remote`, and `components` expose disjoint tool surfaces;
`REMOTE_DEV_STATE_DIR` defaults to `state/kimi/remote` (`AD-02`) and `REMOTE_DEV_HARNESS_PROFILE`
to `kimi`; 9-domain engine provisioning and `Stop` capture delegate to `knowledge_binding`;
native `turn_id` + `last_assistant_message` are preserved and fallback IDs are deterministic.
[Timeouts & Locks] `run_stop_capture` runs with a `6.0s` budget; seed checks take no locks.
[Side-Effect Policy] Provisions 9-domain engine configs on MCP startup, serves stdio MCP,
delegates authorized `Stop` capture to `run_stop_capture`, and prints `{}` on hook exit.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
SEED_SCHEMA = "mindie-harness-seed/1"
API_VERSION = 1
DOMAIN = "vllm-ascend"

from mindie_harness.api import ProfileRef  # noqa: E402
from mindie_harness.atomic import read_json_safe  # noqa: E402
from mindie_harness.mcp.gateway import ManagedMCPAdapter, rebind_native_plugin_if_needed  # noqa: E402
from mindie_harness.mcp.router import MCPRouter  # noqa: E402
from mindie_harness.mcp.server import MCPStdioServer  # noqa: E402
from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding, run_stop_capture  # noqa: E402

class KimiHostAdapter(ManagedMCPAdapter):
    """Kimi host adapter: canonical component/knowledge/remote tool wiring."""

    host = "kimi"

    def __init__(self, *, plugin_root: str | Path = PLUGIN_ROOT, config_path: str | Path | None = None,
                 data_dir: str | Path | None = None) -> None:
        super().__init__("kimi", plugin_root=plugin_root, config_path=config_path, data_dir=data_dir)

    def mcp_tools(self, surface: str, profile: ProfileRef) -> list[dict]:
        tools = super().mcp_tools(surface, profile)
        if str(surface or "").strip().lower() in {"components", "tools"}:
            return [item for item in tools if not str(item.get("name", "")).startswith("knowledge_")]
        return tools

def verify_bundled_seed(source: str | Path | None = None) -> dict:
    """Verify `assets/mindie-harness-seed.pyz` against `assets/seed-lock.json`."""
    root = Path(source) if source is not None else PLUGIN_ROOT
    assets = root if root.name == "assets" else root / "assets"
    lock, pyz = json.loads((assets / "seed-lock.json").read_text(encoding="utf-8-sig")), assets / "mindie-harness-seed.pyz"
    if not isinstance(lock, dict) or lock.get("schema") != SEED_SCHEMA or not pyz.is_file():
        raise ValueError("invalid bundled mindie-harness seed")
    digest, size = hashlib.sha256(), 0
    with pyz.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
            size += len(chunk)
    if size != lock.get("size_bytes") or digest.hexdigest() != lock.get("sha256"):
        raise ValueError("mindie-harness-seed.pyz SHA-256 mismatch")
    return lock

def _read_json(path: Path) -> dict:
    data = read_json_safe(path)
    return data if isinstance(data, dict) else {}

VERSION = str(_read_json(PLUGIN_ROOT / "kimi.plugin.json").get("version") or "0.1.33")
PLUGIN_VERSION = VERSION

def _wire_path(profile: ProfileRef, session: str, cwd: str) -> str | None:
    explicit = os.environ.get("KIMI_CODE_HOME") or _read_json(profile.config_path).get("kimi_home")
    homes = [Path(explicit).expanduser()] if isinstance(explicit, str) and explicit else [Path.home() / ".kimi-code", Path.home() / ".kimi"]
    sessions = next((h / "sessions" for h in homes if (h / "sessions").is_dir()), None)
    if sessions is None or "/" in session or "\\" in session or ".." in session:
        return None
    target_cwd = os.path.normcase(str(Path(cwd).resolve()))
    matches: list[str] = []
    for bucket in sorted(sessions.iterdir()):
        wire = bucket / session / "agents" / "main" / "wire.jsonl"
        if not wire.is_file():
            continue
        state = _read_json(bucket / session / "state.json")
        recorded = next((state.get(k) for k in ("workDir", "work_dir", "cwd", "projectRoot") if isinstance(state.get(k), str) and os.path.isabs(state[k])), None)
        if recorded is not None:
            norm = os.path.normcase(str(Path(recorded).resolve()))
            if os.path.splitdrive(norm)[0] != os.path.splitdrive(target_cwd)[0]:
                continue
            try:
                if os.path.commonpath([target_cwd, norm]) not in {target_cwd, norm}:
                    continue
            except ValueError:
                continue
        matches.append(str(wire.resolve()))
    return matches[0] if len(matches) == 1 else None

def _stop(profile: ProfileRef) -> dict | None:
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(event, dict) or event.get("hook_event_name") not in {None, "Stop"} or event.get("stop_hook_active") not in {None, False}:
        return None
    raw_kind = event.get("identity_kind")
    if raw_kind not in {None, "turn", "notification"}:
        return None
    cwd = event["cwd"] if "cwd" in event else (event.get("workspace") or event.get("project_root"))
    session = event.get("session_id")
    if not isinstance(cwd, str) or not isinstance(session, str) or not session.strip():
        return None
    resolved_cwd = str(Path(cwd).resolve())
    raw_wire = event.get("transcript_path")
    wire = raw_wire if isinstance(raw_wire, str) and raw_wire.strip() else _wire_path(profile, session, cwd)
    raw_turn = event.get("turn_id")
    has_turn = isinstance(raw_turn, str) and bool(raw_turn.strip()) and raw_kind != "notification"
    normalized: dict[str, object] = {
        "hook_event_name": "Stop", "identity_kind": "turn" if has_turn else "notification",
        "session_id": session.strip(), "harness": "kimi", "cwd": resolved_cwd,
    }
    if has_turn:
        normalized["turn_id"] = raw_turn.strip()
        if isinstance(msg := event.get("last_assistant_message"), str) and msg:
            normalized["last_assistant_message"] = msg
    elif isinstance(raw_id := event.get("event_id"), str) and raw_id.strip():
        normalized["event_id"] = raw_id.strip()
    else:
        try:
            size = Path(wire).stat().st_size if wire else 0
        except TimeoutError:
            raise
        except OSError:
            size = 0
        normalized["event_id"] = hashlib.sha256(f"{session.strip()}:{wire or resolved_cwd}:{size}".encode("utf-8")).hexdigest()[:32]
    if wire:
        normalized["transcript_path"] = wire
    return run_stop_capture(profile, normalized, plugin_root=PLUGIN_ROOT, timeout_sec=6.0)

def _serve(surface: str, profile: ProfileRef, version: str = VERSION) -> int:
    try:
        ensure_knowledge_binding(profile, plugin_root=PLUGIN_ROOT, python_executable=sys.executable, include_default_feed=False)
    except TimeoutError:
        raise
    except Exception:
        pass
    adapter = KimiHostAdapter(config_path=profile.config_path)
    router = MCPRouter(profile, surface=surface, tools=adapter.mcp_tools(surface, profile),
                       handler=lambda ctx, arguments: adapter.dispatch_tool(ctx, arguments, profile),
                       session_extractor=adapter.extract_session_identity)
    return MCPStdioServer(router, server_name=f"mindie-{surface}", server_version=version).serve()

def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    config: str | None = None
    if "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args):
            raise SystemExit("--config requires a path")
        config, args = args[index + 1], args[:index] + args[index + 2:]
    profile, version = ProfileRef.resolve("kimi", plugin_root=PLUGIN_ROOT, config_path=config), VERSION
    try:
        rebind_native_plugin_if_needed(profile, native_version=version, runtime_scripts=HERE)
    except TimeoutError:
        raise
    except Exception:
        pass
    os.environ.setdefault("REMOTE_DEV_STATE_DIR", str(profile.state_dir / "remote"))
    os.environ.setdefault("REMOTE_DEV_HARNESS_PROFILE", "kimi")
    if args[:1] == ["mcp"]:
        return _serve(args[1] if len(args) > 1 else "all", profile, version)
    if args[:2] == ["hook", "stop"]:
        try:
            _stop(profile)
        except TimeoutError:
            raise
        except Exception:
            pass
    sys.stdout.buffer.write(b"{}\n")
    sys.stdout.buffer.flush()
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
