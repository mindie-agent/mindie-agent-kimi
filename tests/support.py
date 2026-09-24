from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
FIXTURES = ROOT / "tests" / "fixtures"


def install_inspect_shim():
    """Test-only seam: the pinned core predates Admission.inspect. Provide
    the exact contract of the new-core inspect (status
    active/paused/inactive/missing/unavailable, read-only, no token) so
    adapter tests exercise the same path the merged core will serve."""
    from mindie_knowledge.loop.activation import Admission

    if getattr(Admission, "inspect", None) is not None:
        return
    import sqlite3

    max_failures = getattr(
        __import__("mindie_knowledge.loop.activation", fromlist=["MAX_FAILURES"]),
        "MAX_FAILURES",
        3,
    )

    def inspect(self, session):
        result = dict(status="missing", enabled=False)
        if not isinstance(session, str) or not session.strip() or len(session) > 256:
            return dict(status="unavailable", enabled=False, error_class="ValueError")
        db = None
        try:
            self.path.stat()
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=0.1)
            row = db.execute(
                "SELECT enabled, failures, project_root FROM leases WHERE session=? LIMIT 1",
                (session,),
            ).fetchone()
            if row is not None:
                enabled, failures, project_root = row
                paused = bool(enabled) and failures >= max_failures
                result = dict(
                    status="paused" if paused else "active" if enabled else "inactive",
                    enabled=bool(enabled) and not paused,
                    failures=failures,
                    project_root=project_root,
                )
        except FileNotFoundError:
            pass
        except (OSError, sqlite3.Error, TypeError) as exc:
            result = dict(status="unavailable", enabled=False, error_class=type(exc).__name__)
        finally:
            if db is not None:
                db.close()
        return result

    Admission.inspect = inspect


def env_for(config=None, extra=None, kimi_home=None):
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    if config is not None:
        env["MINDIE_KIMI_CONFIG"] = str(config)
    if kimi_home is not None:
        env["KIMI_CODE_HOME"] = str(kimi_home)
    if extra:
        env.update(extra)
    return env


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
    return path


def make_config(tmp: Path, *, sharing=False, roots=None):
    config = tmp / "kimi.json"
    engine = tmp / "kimi.engine.json"
    community = tmp / "kimi.community.json"
    root = tmp / "domain"
    admission = root / "admission.sqlite3"
    write_json(
        engine,
        dict(
            root=str(root),
            domain="vllm-ascend",
            admission_path=str(admission),
            transcript_adapter=str(SCRIPTS / "transcript.py"),
            agent_command=[sys.executable, str(SCRIPTS / "organizer.py")],
            community_config=str(community),
        ),
    )
    write_json(
        config,
        dict(
            python=sys.executable,
            engine_config=str(engine),
            community_config=str(community),
            state_dir=str(tmp / "adapter-state"),
        ),
    )
    write_json(
        community,
        dict(
            schema="mindie-community-config/1",
            enabled=bool(sharing),
            generation="gen-test" if sharing else "off",
            enabled_at=1.0 if sharing else None,
            repository="owner/repo",
            branch="main",
            project_roots=[str(Path(p).resolve()) for p in (roots or ([tmp] if sharing else []))],
            idle_seconds=300,
            visibility="public",
        ),
    )
    return config


def run_bridge(op, event, config, *, kimi_home=None, timeout=8):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "bridge.py"), op],
        input=json.dumps(event),
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env_for(config, kimi_home=kimi_home),
        cwd=str(ROOT),
    )


def plugin_origin(command, activation_id, args=""):
    return dict(
        kind="plugin_command",
        pluginId="mindie-agent",
        commandName=command,
        commandArgs=args,
        activationId=activation_id,
        trigger="user-slash",
    )


def turn_records(origin, text="cmd", time=2):
    return [
        dict(
            type="turn.prompt",
            origin=origin,
            input=[dict(type="text", text=text)],
            time=time,
        ),
        dict(
            type="context.append_message",
            message=dict(
                role="user",
                content=[dict(type="text", text=text)],
                origin=origin,
            ),
            time=time,
        ),
    ]


def write_session(home: Path, session_id: str, records, *, workdir="wd_test_abc", state=None, cwd=None):
    root = home / "sessions" / workdir / session_id
    wire = root / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True, exist_ok=True)
    wire.write_text("".join(json.dumps(row) + "\n" for row in records))
    payload = dict(title="t", createdAt=1000, cwd=str(cwd or home.resolve()))
    if state:
        payload.update(state)
    write_json(root / "state.json", payload)
    with (home / "session_index.jsonl").open("a") as stream:
        stream.write(
            json.dumps(dict(sessionId=session_id, sessionDir=str(root), workDir="/tmp/proj"))
            + "\n"
        )
    return root, wire
