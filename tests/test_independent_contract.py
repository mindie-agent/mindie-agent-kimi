"""Independent Kimi contract tests.

Phase 2 runs these against candidate 90f73e7 with knowledge core 9beb317.
Expectations follow the user contract and the round-2 review, not the
adapter's private helpers. Scratch stays under this lane. The model boundary
is a local double.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch
from pathlib import Path

from contract_support import pad_records as _pad_records
from contract_support import skill_origin as _skill_origin
from contract_support import user_record as _user_record
from support import (
    SCRIPTS, close_diagnostic_writers,
    make_config,
    plugin_origin,
    run_bridge,
    stop_real_services,
    turn_records,
    write_json,
    write_session,
)

REPO = Path(__file__).resolve().parents[1]

SENTINEL = "PUBLIC_NOTE_grok_kimi_7c1e"
PARENT_SENTINEL = "PARENT_ONLY_NOTE_grok_kimi_11aa"
CHILD_SENTINEL = "CHILD_ONLY_NOTE_grok_kimi_22bb"
MODEL_MARK = b"\n@@GROK_KIMI_MODEL@@\n"
ENV_KEYS = (
    "HOME",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "KIMI_CODE_HOME",
    "KIMI_PLUGIN_ROOT",
    "MINDIE_KIMI_CONFIG",
    "MINDIE_DIAGNOSTICS_CONFIG",
    "MINDIE_DIAGNOSTICS_ROOT",
    "MINDIE_CC_CONFIG",
)

DOUBLE_SOURCE = textwrap.dedent(
    """\
    import json, sys
    from pathlib import Path
    from mindie_knowledge.materials import summarizer
    log = Path(sys.argv[1])
    raw = sys.stdin.buffer.read()
    if "--identity" in sys.argv[2:]:
        # The identity probe is not a model call; it is never counted.
        sys.stdout.write(summarizer.canonical(summarizer.policy_identity(
            model="double", effort="off", implementation={"double": "1"})) + "\\n")
        raise SystemExit(0)
    with log.open("ab") as handle:
        handle.write(b"\\n@@GROK_KIMI_MODEL@@\\n" + raw)
    request = summarizer.validate_request(json.loads(raw))
    result = {
        "blocks": [dict(block_id=block["block_id"], title="Observed increment",
                        summary="Public material was organized once.")
                   for block in request["blocks"]],
        "navigation": {"title": "Observed increment",
                       "summary": "Public material was organized once."},
    }
    sys.stdout.write(summarizer.canonical(summarizer.outcome(
        request, status="returned", result=result, model_calls=1,
        usage_known=False, elapsed_ms=1)) + "\\n")
    """
)


def _under(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    if path != root and root not in path.parents:
        raise AssertionError(f"path escaped the lane scratch: {path}")
    return path


def _model_calls(log: Path) -> int:
    if not log.is_file():
        return 0
    return log.read_bytes().count(MODEL_MARK)


def _model_text(log: Path) -> str:
    if not log.is_file():
        return ""
    return log.read_bytes().decode("utf-8", "replace")


def _kill_wakes(root: Path) -> None:
    for wake in root.rglob("wake.json"):
        try:
            data = json.loads(wake.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for key in ("service_pid", "wake_pid"):
            pid = data.get(key)
            if not isinstance(pid, int) or pid <= 1:
                continue
            if os.name == "nt":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=5)
                continue
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        os.kill(pid, sig)
                    except (ProcessLookupError, PermissionError, OSError):
                        pass


def _store_path(tmp: Path) -> Path:
    from mindie_knowledge.state_layout import state_root

    return state_root(tmp / "domain", "vllm-ascend") / "state-v4.sqlite3"


def _captures(tmp: Path):
    path = _store_path(tmp)
    if not path.is_file():
        return []
    db = sqlite3.connect(path)
    try:
        return [
            dict(id=row[0], status=row[1], transcript=row[2], session=row[3])
            for row in db.execute(
                "SELECT id, status, transcript, session FROM captures ORDER BY created"
            )
        ]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def _entries(tmp: Path):
    path = _store_path(tmp)
    if not path.is_file():
        return []
    db = sqlite3.connect(path)
    try:
        return [row[0] for row in db.execute("SELECT doc FROM entries")]
    except sqlite3.Error:
        return []
    finally:
        db.close()


def _prepare_store(tmp: Path) -> None:
    from mindie_knowledge.loop.store import Store

    store = Store(tmp / "domain", "vllm-ascend")
    store.close()


def _point_agent(tmp: Path, script: Path, log: Path) -> None:
    path = tmp / "kimi.engine.json"
    data = json.loads(path.read_text())
    data.pop("agent_command", None)
    data["summary_command"] = [sys.executable, str(script), str(log)]
    path.write_text(json.dumps(data, indent=2) + "\n")


def _install_double(tmp: Path):
    script = tmp / "model_double.py"
    log = tmp / "model-calls.log"
    script.write_text(DOUBLE_SOURCE)
    log.unlink(missing_ok=True)
    return script, log


def _drain_pending(tmp: Path, log: Path) -> None:
    """Run the production worker once on a capture Stop already persisted.

    A detached wake is killed first so the model double is counted once.
    """
    _kill_wakes(tmp)
    time.sleep(0.2)
    _kill_wakes(tmp)
    if _model_calls(log):
        return
    pending = [
        row for row in _captures(tmp)
        if row["status"] in {"queued", "pending", "deferred"}
    ]
    if not pending:
        return
    engine_doc = json.loads((tmp / "kimi.engine.json").read_text())
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop.store import Store
    from mindie_knowledge.loop.transcript_capture import summarize_due
    import transcript as transcript_mod

    store = Store(tmp / "domain", "vllm-ascend")
    try:
        engine = Engine(
            store,
            capture_mode="public-transcript",
            redactor_executable=engine_doc["redactor_executable"],
            summary_command=engine_doc["summary_command"],
            settings_path=engine_doc["community_config"],
            admission=Admission(engine_doc["admission_path"]),
            transcript_adapter=transcript_mod,
        )
        for row in pending:
            if _model_calls(log):
                break
            current = store.capture_row(row["id"])
            if current and current["status"] in {"queued", "pending", "deferred"}:
                engine._process(row["id"])
        if not _model_calls(log):
            # The summary stage runs in its own core loop, not in capture.
            engine.last_activity = 0
            summarize_due(engine)
    finally:
        store.close()


def _stop(tmp: Path, session: str, home: Path, log: Path, cwd: Path):
    _under(cwd, tmp)
    result = run_bridge(
        "stop",
        {
            "hook_event_name": "Stop",
            "session_id": session,
            "cwd": str(cwd),
            "stop_hook_active": False,
        },
        tmp / "kimi.json",
        kimi_home=home,
        timeout=20,
    )
    _drain_pending(tmp, log)
    return result


def _attach_consent_pointer(tmp: Path) -> None:
    """Point the designated community file at this profile's consent document."""
    path = tmp / "kimi.community.json"
    data = json.loads(path.read_text())
    data["consent_config"] = str((tmp / "mindie-consent.json").resolve())
    path.write_text(json.dumps(data, indent=2) + "\n")


def _write_consent(tmp: Path, *, choice=None, reporting=None, raw=None):
    path = tmp / "mindie-consent.json"
    if raw is not None:
        path.write_text(raw)
        return path
    data = {"schema": "mindie-consent/1"}
    if choice is not None:
        data["choice"] = choice
        data["choice_at"] = 10
    if reporting is not None:
        data["reporting"] = reporting
        data["reporting_at"] = 10
    path.write_text(json.dumps(data) + "\n")
    return path


def _reap(procs) -> None:
    """Collect every child this test started. A failed assertion must not leave one."""
    for proc in procs:
        if proc is None:
            continue
        if proc.poll() is None:
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()


class LaneIsolation(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        reporter = patch("mindie_diagnostics.reporting_commands.ensure",
                         return_value={"status": "degraded", "category": "test_no_native_service"})
        reporter.start()
        self.addCleanup(reporter.stop)
        explicit = os.environ.get("MINDIE_TEST_ROOT")
        if explicit:
            root = Path(explicit).expanduser().resolve()
            root.mkdir(parents=True, exist_ok=True)
            self.tmp = Path(tempfile.mkdtemp(prefix="case-", dir=root))
            self._scratch = None
        else:
            self._scratch = tempfile.TemporaryDirectory(prefix="mindie-kimi-contract-")
            self.tmp = Path(self._scratch.name)
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        home = self.tmp / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "data")
        os.environ["XDG_STATE_HOME"] = str(self.tmp / "xdg-state")
        os.environ["KIMI_PLUGIN_ROOT"] = str(REPO)
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(self.tmp / "diag-root")
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.tmp / "diagnostics.json")
        os.environ.pop("MINDIE_CC_CONFIG", None)
        sys.path.insert(0, str(SCRIPTS))
        for name in (
            "entry", "entry_state", "consent", "paths", "sharing",
            "identity", "admission", "bridge", "transcript",
        ):
            sys.modules.pop(name, None)

    def tearDown(self):
        close_diagnostic_writers(self.tmp / "diag-root")
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import shutil

        _kill_wakes(self.tmp)
        # A real spawned service outlives its fixture unless stopped
        # explicitly; on Windows its open handles otherwise pin the store.
        stop_real_services(self.tmp / "kimi.engine.json")
        if self._scratch is None:
            shutil.rmtree(self.tmp, ignore_errors=True)
        else:
            self._scratch.cleanup()

    def _stub_service(self):
        import knowledge_service

        original = knowledge_service.ensure_service
        knowledge_service.ensure_service = lambda *_args, **_kwargs: None
        self.addCleanup(lambda: setattr(knowledge_service, "ensure_service", original))

    def _configured(self, *, sharing=True):
        project = self.tmp / "project"
        project.mkdir(exist_ok=True)
        store_dir = self.tmp / "domain" / "vllm-ascend"
        if store_dir.exists():
            import shutil

            shutil.rmtree(store_dir, ignore_errors=True)
        config = make_config(self.tmp, sharing=sharing, roots=[project])
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        _prepare_store(self.tmp)
        script, log = _install_double(self.tmp)
        _point_agent(self.tmp, script, log)
        return project, log


class ConsentGateTests(LaneIsolation):
    """community.enabled must not outrank the saved consent choice."""

    def _arm(self, text, session="ses_gate"):
        project, log = self._configured(sharing=True)
        os.environ["KIMI_CODE_HOME"] = str(self.tmp / "kimi-home")
        import admission

        admission.activate(session, project_root=str(project.resolve()))
        when = int((time.time() + 30) * 1000)
        home = self.tmp / "kimi-home"
        write_session(
            home,
            session,
            [_user_record(text, when)],
            cwd=project,
        )
        return project, log, home

    def _run(self, session, project, home, log):
        result = _stop(self.tmp, session, home, log, project)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout or "{}"), {})
        return _captures(self.tmp), _model_calls(log), _model_text(log)

    def test_contribute_stop_persists_and_worker_reads_once(self):
        project, log, home = self._arm(f"Map device 8. {SENTINEL}")
        _write_consent(self.tmp, choice="contribute", reporting="disabled")
        captures, calls, text = self._run("ses_gate", project, home, log)
        self.assertTrue(captures, f"Stop did not persist a capture: {captures}")
        self.assertEqual(calls, 1, f"model calls={calls} captures={captures}")
        self.assertIn(SENTINEL, text)
        docs = _entries(self.tmp)
        self.assertTrue(
            any("Public material was organized once." in doc for doc in docs),
            docs,
        )
        again = _stop(self.tmp, "ses_gate", home, log, project)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(
            _model_calls(log), 1,
            "a second Stop of unchanged material called the model again",
        )

    def test_explicit_community_off_blocks_even_when_consent_says_contribute(self):
        project, log, home = self._arm("should not be read")
        community = json.loads((self.tmp / "kimi.community.json").read_text())
        community["enabled"] = False
        community["enabled_at"] = None
        (self.tmp / "kimi.community.json").write_text(json.dumps(community) + "\n")
        _write_consent(self.tmp, choice="contribute", reporting="disabled")
        captures, calls, _text = self._run("ses_gate", project, home, log)
        self.assertEqual(calls, 0, captures)
        self.assertEqual(captures, [])

    def test_wired_disallowed_or_damaged_consent_does_not_collect(self):
        """After consent_config is wired, the saved choice gates the worker.

        A legacy file with no pointer is read-compatibility until the entry
        boundary (see the boundary test). It is not, by itself, this case.
        """
        cases = (
            ("read-only", dict(choice="read-only", reporting="disabled")),
            ("later", dict(choice="later", reporting="later")),
            ("disabled", dict(choice="disabled", reporting="disabled")),
            ("corrupt", dict(raw="{broken-consent")),
        )
        failures = []
        for name, spec in cases:
            with self.subTest(name=name):
                session = "ses_" + name.replace("-", "_")
                project, log, home = self._arm(
                    f"synthetic {name} {SENTINEL}",
                    session=session,
                )
                _write_consent(self.tmp, **spec)
                _attach_consent_pointer(self.tmp)
                import entry

                status = entry.status_payload(session)
                captures, calls, text = self._run(session, project, home, log)
                if calls or captures or SENTINEL in text:
                    failures.append(dict(
                        case=name,
                        model_calls=calls,
                        captures=captures,
                        status_choices=status.get("choices"),
                        consent_state=status.get("consent_state"),
                        hint=status.get("hint"),
                        sharing_enabled=(status.get("sharing") or {}).get("enabled"),
                        read_sentinel=SENTINEL in text,
                    ))
        self.assertEqual(failures, [])

    def test_entry_boundary_closes_a_legacy_enabled_file(self):
        """Saved read-only plus an old enabled file must not survive the entry."""
        project, log, home = self._arm(f"legacy bypass {SENTINEL}")
        _write_consent(self.tmp, choice="read-only", reporting="disabled")
        origin = _skill_origin("act-boundary")
        write_session(
            home, "ses_gate",
            [dict(
                type="turn.steer", time=int((time.time() + 40) * 1000),
                origin=origin,
                input=[dict(type="text", text="/mindie-agent")],
            )],
            cwd=project,
        )
        self._stub_service()
        import entry

        entry.op_init("ses_gate", str(project))
        captures, calls, text = self._run("ses_gate", project, home, log)
        self.assertEqual(calls, 0, captures)
        self.assertEqual(captures, [])
        self.assertNotIn(SENTINEL, text)

    def test_corrupt_shared_authority_is_not_replaced_by_an_enabled_legacy(self):
        project, log, home = self._arm(f"do not fall back {SENTINEL}")
        legacy = self.tmp / "kimi.community.json"
        legacy_before = legacy.read_bytes()
        shared = self.tmp / "mindie-community.json"
        shared.write_text("{broken-shared")
        for name in ("kimi.json", "kimi.engine.json"):
            path = self.tmp / name
            data = json.loads(path.read_text())
            data["community_config"] = str(shared.resolve())
            path.write_text(json.dumps(data, indent=2) + "\n")
        import entry

        status = entry.status_payload("ses_gate")
        captures, calls, text = self._run("ses_gate", project, home, log)
        sharing = status.get("sharing") or {}
        self.assertNotEqual(sharing.get("state"), "enabled", sharing)
        self.assertNotEqual(sharing.get("enabled"), True, sharing)
        self.assertEqual(calls, 0, captures)
        self.assertEqual(captures, [])
        self.assertNotIn(SENTINEL, text)
        self.assertEqual(legacy.read_bytes(), legacy_before)
        engine = json.loads((self.tmp / "kimi.engine.json").read_text())
        self.assertEqual(engine["community_config"], str(shared.resolve()))

    def test_sibling_enabled_file_cannot_override_a_closed_authority(self):
        project, log, home = self._arm("must stay local")
        configured = self.tmp / "kimi.community.json"
        original = configured.read_bytes()
        configured_doc = json.loads(original)
        configured_doc["enabled"] = False
        configured_doc["enabled_at"] = None
        configured_doc["project_roots"] = [str(project.resolve())]
        configured.write_text(json.dumps(configured_doc, indent=2) + "\n")
        sibling = self.tmp / "mindie-community.json"
        wider = self.tmp / "wider-project"
        wider.mkdir()
        sibling.write_text(json.dumps(dict(
            configured_doc,
            enabled=True,
            enabled_at=time.time(),
            generation="gen-wider",
            project_roots=[str(project.resolve()), str(wider.resolve())],
        )) + "\n")
        sibling_before = sibling.read_bytes()
        configured_before = configured.read_bytes()
        adapter_before = (self.tmp / "kimi.json").read_bytes()
        engine_before = (self.tmp / "kimi.engine.json").read_bytes()
        _write_consent(self.tmp, choice="read-only", reporting="disabled")
        captures, calls, _text = self._run("ses_gate", project, home, log)
        import entry

        status = entry.status_payload("ses_gate")
        sharing = status.get("sharing") or {}
        problems = []
        if calls or captures:
            problems.append(f"collected model_calls={calls} captures={captures}")
        if sharing.get("enabled") is not False:
            problems.append(f"status sharing={sharing}")
        if sharing.get("project_roots") != 1:
            problems.append(f"status roots={sharing.get('project_roots')}")
        self.assertEqual(problems, [])
        self.assertEqual(configured.read_bytes(), configured_before)
        self.assertEqual(sibling.read_bytes(), sibling_before)
        self.assertEqual((self.tmp / "kimi.json").read_bytes(), adapter_before)
        self.assertEqual((self.tmp / "kimi.engine.json").read_bytes(), engine_before)
        saved_roots = json.loads(configured.read_text())["project_roots"]
        self.assertEqual(saved_roots, [str(project.resolve())])

    def test_status_read_does_not_rewrite_community_files(self):
        self._configured(sharing=True)
        adapter_before = (self.tmp / "kimi.json").read_bytes()
        engine_before = (self.tmp / "kimi.engine.json").read_bytes()
        legacy_before = (self.tmp / "kimi.community.json").read_bytes()
        import entry

        entry.status_payload(None)
        shared = self.tmp / "mindie-community.json"
        problems = []
        if shared.exists():
            problems.append("created " + shared.name)
        if (self.tmp / "kimi.json").read_bytes() != adapter_before:
            problems.append("adapter config rewritten: " + (self.tmp / "kimi.json").read_text())
        if (self.tmp / "kimi.engine.json").read_bytes() != engine_before:
            problems.append("engine config rewritten: " + (self.tmp / "kimi.engine.json").read_text())
        if (self.tmp / "kimi.community.json").read_bytes() != legacy_before:
            problems.append("legacy community rewritten")
        self.assertEqual(problems, [])

    def test_consent_read_does_not_migrate_or_repair(self):
        self._configured(sharing=False)
        from paths import first_use_path
        import consent

        marker = first_use_path()
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"choice": "read-only"}))
        consent_path = consent.consent_path()
        self.assertFalse(consent_path.exists())
        loaded = consent.load()
        self.assertFalse(
            consent_path.exists(),
            f"reading consent wrote {consent_path} from a legacy marker: {loaded}",
        )
        self.assertEqual(loaded["state"], "missing")
        self.assertIsNone(loaded["choice"])


    def test_hand_written_consent_pointer_still_does_not_stop_collection(self):
        """A wired consent_config plus read-only must stop Stop and the worker."""
        project, log, home = self._arm(f"pointer case {SENTINEL}")
        consent_file = _write_consent(
            self.tmp, choice="read-only", reporting="disabled",
        )
        for name in ("kimi.community.json", "kimi.engine.json"):
            path = self.tmp / name
            if name.endswith("engine.json"):
                continue
            data = json.loads(path.read_text())
            data["consent_config"] = str(consent_file.resolve())
            path.write_text(json.dumps(data, indent=2) + "\n")
        captures, calls, text = self._run("ses_gate", project, home, log)
        self.assertEqual(calls, 0, captures)
        self.assertEqual(captures, [])
        self.assertNotIn(SENTINEL, text)

    def test_enable_path_records_consent_config_pointer(self):
        project, _log = self._configured(sharing=False)
        import consent
        import sharing

        sharing.write_enabled(
            repository="owner/repo",
            project_roots=[str(project.resolve())],
            account="alice",
            visibility="public",
        )
        import consent as consent_mod

        path = consent_mod.configured_community_path()
        data = json.loads(Path(path).read_text())
        pointer = data.get("consent_config")
        self.assertIsInstance(pointer, str)
        self.assertTrue(Path(pointer).is_absolute())
        self.assertEqual(Path(pointer).resolve(), consent.consent_path().resolve())

    def test_fork_capture_drops_inherited_material(self):
        project, log = self._configured(sharing=True)
        _write_consent(self.tmp, choice="contribute", reporting="disabled")
        import admission

        session = "ses_fork_child"
        admission.activate(session, project_root=str(project.resolve()))
        created = int((time.time() + 5) * 1000)
        home = self.tmp / "kimi-home"
        records = [
            _user_record(PARENT_SENTINEL, created - 5000),
            _user_record(CHILD_SENTINEL, created + 5000),
        ]
        write_session(
            home, session, records, cwd=project,
            state={"forkedFrom": "ses_fork_parent", "createdAt": created},
        )
        result = _stop(self.tmp, session, home, log, project)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = _model_text(log)
        self.assertIn(CHILD_SENTINEL, text)
        self.assertNotIn(PARENT_SENTINEL, text)
        self.assertEqual(_model_calls(log), 1)

    def test_worker_fault_does_not_reset_the_saved_choice(self):
        project, log, home = self._arm(f"fault case {SENTINEL}")
        _write_consent(self.tmp, choice="contribute", reporting="later")
        before = json.loads((self.tmp / "mindie-consent.json").read_text())
        (self.tmp / "model_double.py").write_text("import sys\nsys.exit(3)\n")
        result = _stop(self.tmp, "ses_gate", home, log, project)
        self.assertEqual(result.returncode, 0, result.stderr)
        after = json.loads((self.tmp / "mindie-consent.json").read_text())
        self.assertEqual(after["choice"], before["choice"])
        self.assertEqual(after["reporting"], before["reporting"])
        docs = _entries(self.tmp)
        self.assertFalse(any(SENTINEL in doc for doc in docs))
        self.assertFalse(
            any("Public material was organized once." in doc for doc in docs)
        )
        import entry

        payload = entry.status_payload("ses_gate")
        self.assertEqual(payload.get("choices"), [])
        self.assertNotIn("reporting_choice", payload)
        self.assertEqual(payload.get("first_use"), "contribute")


class EntryBoundaryTests(LaneIsolation):
    def _home(self, session, records, cwd=None):
        home = self.tmp / "kimi-home"
        write_session(home, session, records, cwd=cwd or self.tmp)
        os.environ["KIMI_CODE_HOME"] = str(home)
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.tmp / "absent.json")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        return home

    def test_turn_opener_beyond_tail_window_still_binds(self):
        self._configured(sharing=False)
        import identity

        near = [
            dict(
                type="turn.steer",
                time=2,
                origin=_skill_origin("act-near", args="read-only"),
                input=[dict(type="text", text="/mindie-agent read-only")],
            ),
            *_pad_records(3, 1),
        ]
        self._home("ses_near", near)
        near_hit = identity.require_current_entry("ses_near", "init")
        self.assertEqual(near_hit["activation_id"], "act-near")

        far = [
            dict(
                type="turn.steer",
                time=2,
                origin=_skill_origin("act-far", args="read-only"),
                input=[dict(type="text", text="/mindie-agent read-only")],
            ),
            *_pad_records(3, 80),
        ]
        write_session(self.tmp / "kimi-home", "ses_far", far, cwd=self.tmp, workdir="wd_far")
        wire = (
            self.tmp / "kimi-home" / "sessions" / "wd_far" / "ses_far"
            / "agents" / "main" / "wire.jsonl"
        )
        self.assertGreater(wire.stat().st_size, 256 * 1024)
        try:
            found = identity.require_current_entry("ses_far", "init")
        except ValueError as exc:
            self.fail(
                f"turn opener {wire.stat().st_size} bytes from EOF was rejected: {exc}"
            )
        self.assertEqual(found["activation_id"], "act-far")

    def test_in_turn_user_slash_beyond_tail_window_still_binds(self):
        self._configured(sharing=False)
        import identity

        records = [
            _user_record("/mindie-agent read-only", 2),
            *_pad_records(3, 80),
            dict(
                type="turn.steer",
                time=90,
                origin=_skill_origin(
                    "act-inturn", trigger="model-tool", in_turn=True, args="read-only",
                ),
                input=[dict(type="text", text="skill loaded")],
            ),
        ]
        self._home("ses_inturn", records)
        wire = (
            self.tmp / "kimi-home" / "sessions" / "wd_test_abc" / "ses_inturn"
            / "agents" / "main" / "wire.jsonl"
        )
        self.assertGreater(wire.stat().st_size, 256 * 1024)
        try:
            found = identity.require_current_entry("ses_inturn", "init")
        except ValueError as exc:
            self.fail(
                f"in-turn /mindie-agent text beyond the tail was rejected: {exc}"
            )
        self.assertEqual(found["activation_id"], "act-inturn")

    def test_mention_inside_the_user_text_is_not_the_entry(self):
        self._configured(sharing=False)
        import identity

        records = [
            _user_record("see /mindie-agent for the docs", 2),
            dict(
                type="turn.steer",
                time=3,
                origin=_skill_origin("act-mention", trigger="model-tool", in_turn=True),
                input=[dict(type="text", text="skill loaded")],
            ),
        ]
        self._home("ses_mention", records)
        with self.assertRaises(ValueError):
            identity.require_current_entry("ses_mention", "init")

    def test_fork_does_not_reuse_an_inherited_activation(self):
        project, _log = self._configured(sharing=False)
        import admission
        import entry

        parent_records = turn_records(
            _skill_origin("act-parent", args="read-only"),
            "/mindie-agent read-only",
        )
        home = self.tmp / "kimi-home"
        write_session(home, "ses_parent", parent_records, cwd=project)
        os.environ["KIMI_CODE_HOME"] = str(home)
        write_session(
            home, "ses_child", parent_records, cwd=project, workdir="wd_child",
            state={"forkedFrom": "ses_parent", "createdAt": 40_000},
        )
        self._stub_service()
        try:
            bound = entry.op_init("ses_child", str(project))
        except ValueError as exc:
            bound = exc
        lease = admission.gate().active_lease("ses_child")
        self.assertIsInstance(
            bound, ValueError,
            f"inherited activation bound the fork: {bound!r} lease={lease!r}",
        )
        self.assertIsNone(lease)

    def test_contribute_does_not_require_the_sharing_enable_command(self):
        project, _log = self._configured(sharing=False)
        said = "contribute owner/repo alice"
        home = self.tmp / "kimi-home"
        write_session(
            home, "ses_contrib",
            turn_records(dict(kind="user"), said),
            cwd=project,
        )
        os.environ["KIMI_CODE_HOME"] = str(home)
        self._stub_service()
        import entry
        import sharing

        problems = []
        cold = entry.status_payload(None)
        self.assertEqual(cold["experience"], "needs-configuration")
        self.assertEqual(cold["choices"], [])
        contribute = cold["required"]
        if "sharing-enable" in json.dumps(contribute):
            problems.append("configuration requires a separate sharing-enable command")
        try:
            entry.dispatch(
                "ses_contrib", str(project), "choose", choice="contribute",
                repository="other/repo", account="mallory",
            )
            problems.append("destination absent from the user reply was accepted")
        except ValueError:
            pass
        try:
            payload = entry.dispatch(
                "ses_contrib", str(project), "choose", choice="contribute",
                repository="owner/repo", account="alice",
            )
        except ValueError as exc:
            problems.append(f"choose rejected: {exc}")
        else:
            if payload.get("choices"):
                problems.append(f"choices still offered: {payload.get('choices')}")
            if "sharing-enable" in json.dumps(payload):
                problems.append("entry response names sharing-enable")
            import consent

            saved = json.loads(consent.consent_path().read_text())
            if saved.get("choice") != "contribute":
                problems.append(f"stored choice={saved.get('choice')}")
            roots = [str(path.resolve()) for path in sharing.load().project_roots]
            if roots != [str(project.resolve())]:
                problems.append(f"roots={roots}")
        self.assertEqual(problems, [])

    def test_contribution_scope_comes_from_the_host_session(self):
        project, _log = self._configured(sharing=False)
        host = project
        foreign = self.tmp / "foreign-root"
        foreign.mkdir()
        args = (
            "--repository owner/repo --account me "
            f"--project-root {foreign} --visibility public"
        )
        home = self.tmp / "kimi-home"
        write_session(
            home,
            "ses_scope",
            turn_records(plugin_origin("sharing-enable", "act-scope", args), args),
            cwd=host,
        )
        os.environ["KIMI_CODE_HOME"] = str(home)
        import consent
        import entry
        from paths import load_engine_config

        _write_consent(self.tmp, choice="read-only", reporting="later")
        try:
            result = entry.op_sharing_enable("ses_scope")
        except ValueError as exc:
            result = exc
        # Rejecting a root that is not the session cwd is enough. Swallowing
        # the flag and enabling that directory is not.
        self.assertIsInstance(
            result, ValueError,
            f"conflicting --project-root was reported as success: {result!r}",
        )
        saved = consent.load()
        self.assertEqual(saved.get("choice"), "read-only")
        self.assertEqual(saved.get("reporting"), "later")
        pointed = Path(load_engine_config()["community_config"])
        settings = json.loads(pointed.read_text())
        self.assertIsNot(settings.get("enabled"), True)
        roots = [str(Path(item).resolve()) for item in settings.get("project_roots") or []]
        self.assertNotIn(str(foreign.resolve()), roots)

    def test_second_session_and_fork_do_not_present_choices_again(self):
        project, _log = self._configured(sharing=False)
        _write_consent(self.tmp, choice="read-only", reporting="later")
        before = json.loads((self.tmp / "mindie-consent.json").read_text())
        home = self.tmp / "kimi-home"
        write_session(
            home, "ses_two",
            turn_records(_skill_origin("act-two"), "/mindie-agent"),
            cwd=project,
        )
        os.environ["KIMI_CODE_HOME"] = str(home)
        self._stub_service()
        import entry

        def assert_quiet(payload):
            self.assertEqual(payload.get("choices"), [])
            self.assertNotIn("reporting_choice", payload)
            offered = [
                item.get("id") for item in payload.get("choices") or []
                if isinstance(item, dict)
            ]
            self.assertEqual(offered, [])

        assert_quiet(entry.op_init("ses_two", str(project)))
        created = int(time.time() * 1000)
        write_session(
            home, "ses_fork_quiet",
            turn_records(_skill_origin("act-fork"), "/mindie-agent", time=created),
            cwd=project, workdir="wd_fork_quiet",
            state={"forkedFrom": "ses_two", "createdAt": created},
        )
        assert_quiet(entry.op_init("ses_fork_quiet", str(project)))
        after = json.loads((self.tmp / "mindie-consent.json").read_text())
        self.assertEqual(after["choice"], before["choice"])
        self.assertEqual(after["reporting"], before["reporting"])


    def test_cc_adapter_reads_the_same_profile_consent(self):
        """A sibling adapter config in the same profile directory shares the consent file."""
        self._configured(sharing=False)
        import consent

        _write_consent(self.tmp, choice="later", reporting="disabled")
        from unittest import mock

        cc_config = self.tmp / "cc.json"
        cc_config.write_text("{}\n")
        with mock.patch.dict(os.environ, {"MINDIE_KIMI_CONFIG": str(cc_config)}):
            loaded = consent.load()
        self.assertEqual(loaded["choice"], "later")
        self.assertEqual(loaded["reporting"], "disabled")
        self.assertEqual(loaded["state"], "ok")

    def test_isolated_profile_does_not_inherit_choice(self):
        self._configured(sharing=False)
        import consent

        consent.record_choice("later")
        other = self.tmp / "other-profile"
        other.mkdir()
        (other / "kimi.json").write_text("{}\n")
        code = (
            "import json, os, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "os.environ['MINDIE_KIMI_CONFIG'] = sys.argv[2]\n"
            "import consent\n"
            "print(json.dumps(consent.load()))\n"
        )
        isolated = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPTS), str(other / "kimi.json")],
            capture_output=True, text=True, timeout=10, env=os.environ.copy(),
        )
        self.assertEqual(isolated.returncode, 0, isolated.stderr)
        self.assertEqual(json.loads(isolated.stdout)["state"], "missing")
        self.assertIsNone(json.loads(isolated.stdout)["choice"])

    def test_prefix_trap_is_not_an_entry(self):
        self._configured(sharing=False)
        import identity

        records = [
            _user_record("/mindie-agentuous please", 2),
            dict(
                type="turn.steer",
                time=3,
                origin=_skill_origin("act-prefix", trigger="model-tool", in_turn=True),
                input=[dict(type="text", text="skill loaded")],
            ),
        ]
        self._home("ses_prefix", records)
        with self.assertRaises(ValueError):
            identity.require_current_entry("ses_prefix", "init")

    def test_long_log_parser_still_reads_the_early_record(self):
        """The production parser, unlike the entry tail window, must see
        material that is far from EOF. This distinguishes the two mechanisms.
        """
        self._configured(sharing=False)
        records = [
            _user_record(SENTINEL, int((time.time() + 5) * 1000)),
            *_pad_records(int((time.time() + 6) * 1000), 80),
        ]
        self._home("ses_long", records)
        wire = (
            self.tmp / "kimi-home" / "sessions" / "wd_test_abc" / "ses_long"
            / "agents" / "main" / "wire.jsonl"
        )
        self.assertGreater(wire.stat().st_size, 256 * 1024)
        import transcript

        read = transcript.read_material(str(wire), 0, session_id="ses_long")
        self.assertIn(SENTINEL, read.get("text") or "")
        self.assertGreater(read.get("records") or 0, 0)

    def test_newer_user_turn_is_not_an_earlier_activation(self):
        """A later ordinary turn must not inherit an earlier activation."""
        project, _log = self._configured(sharing=False)
        import identity

        records = [
            dict(
                type="turn.steer", time=2,
                origin=_skill_origin("act-old", args="read-only"),
                input=[dict(type="text", text="/mindie-agent read-only")],
            ),
            _user_record("continue the refactor", 3),
        ]
        self._home("ses_later_turn", records, cwd=project)
        with self.assertRaises(ValueError):
            identity.require_current_entry("ses_later_turn", "init")



    def test_user_disable_survives_a_concurrent_settings_stamp(self):
        """A settings stamp must not write back enabled=true over a user disable."""
        self._configured(sharing=True)
        adapter = (self.tmp / "kimi.json").read_bytes()
        engine = (self.tmp / "kimi.engine.json").read_bytes()
        community = (self.tmp / "kimi.community.json").read_bytes()
        script = (
            "import os, sys, time\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "os.environ['MINDIE_KIMI_CONFIG'] = sys.argv[2]\n"
            "open(sys.argv[3], 'w').close()\n"
            "deadline = time.time() + 5\n"
            "while not os.path.exists(sys.argv[4]):\n"
            "    if time.time() > deadline:\n"
            "        raise SystemExit('peer did not reach the barrier')\n"
            "    time.sleep(0.01)\n"
            "import consent, sharing\n"
            "sharing.write_disabled() if sys.argv[5]=='disable' "
            "else consent.adopt_community_settings()\n"
        )
        lost = []
        for _round in range(40):
            (self.tmp / "kimi.json").write_bytes(adapter)
            (self.tmp / "kimi.engine.json").write_bytes(engine)
            (self.tmp / "kimi.community.json").write_bytes(community)
            shared = self.tmp / "mindie-community.json"
            shared.unlink(missing_ok=True)
            for extra in self.tmp.glob("*.lock"):
                extra.unlink()
            ready = self.tmp / f"stamp-{_round}"
            ready.mkdir()
            procs = []
            crashed = []
            try:
                for kind, peer in (("disable", "stamp"), ("stamp", "disable")):
                    procs.append(subprocess.Popen(
                        [sys.executable, "-c", script, str(SCRIPTS),
                         str(self.tmp / "kimi.json"), str(ready / kind),
                         str(ready / peer), kind],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    ))
                for proc in procs:
                    _out, err = proc.communicate(timeout=15)
                    if proc.returncode != 0:
                        crashed.append(err)
            finally:
                _reap(procs)
            if crashed:
                lost.append(crashed)
                break
            from paths import load_engine_config

            pointed = Path(load_engine_config()["community_config"])
            try:
                final = json.loads(pointed.read_text())
            except (OSError, ValueError) as exc:
                lost.append(f"authority unreadable: {exc}")
                break
            if final.get("enabled") is not False:
                lost.append(dict(path=str(pointed), enabled=final.get("enabled")))
                break
        self.assertEqual(lost, [])

    def test_adopt_reloads_adapter_config_inside_the_write_lock(self):
        """A config write that lands after adopt has loaded, but before it
        acquires the profile lock, must not be replaced by the stale snapshot.
        """
        self._configured(sharing=True)
        import consent
        from mindie_knowledge.loop.settings import CommunityWriteContext

        shared = consent.shared_community_path()
        adapter_path = self.tmp / "kimi.json"
        lock = CommunityWriteContext(str(shared))
        lock.__enter__()
        script = (
            "import os, sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "os.environ['MINDIE_KIMI_CONFIG'] = sys.argv[2]\n"
            "import consent\n"
            "import mindie_knowledge.consent_store as store\n"
            "real_lock = store._lock_file_nb\n"
            "def observe_lock(fd):\n"
            "    try: return real_lock(fd)\n"
            "    except OSError:\n"
            "        open(sys.argv[3], 'w').close()\n"
            "        raise\n"
            "store._lock_file_nb = observe_lock\n"
            "consent.adopt_community_settings()\n"
        )
        waiting = self.tmp / "real-lock-contended"
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(SCRIPTS), str(adapter_path), str(waiting)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        # Observe a real kernel-lock contention, without replacing its result.
        # This white-box barrier does not depend on lsof or startup timing.
        blocked = False
        err = ""
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    break
                if waiting.exists():
                    blocked = True
                    break
                time.sleep(0.05)
            if blocked:
                current = json.loads(adapter_path.read_text())
                current["lane_marker"] = "keep-me"
                current["community_config"] = str(shared.resolve())
                adapter_path.write_text(json.dumps(current, indent=2) + "\n")
        finally:
            lock.__exit__(None, None, None)
            if proc.poll() is None:
                try:
                    _out, err = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    _out, err = proc.communicate()
            else:
                _out, err = proc.communicate()
        self.assertTrue(
            blocked,
            "adopt did not reach the profile lock"
            + (f": {err.strip()[:400]}" if err else ""),
        )
        self.assertEqual(proc.returncode, 0, err)
        saved = json.loads(adapter_path.read_text())
        self.assertEqual(
            saved.get("lane_marker"), "keep-me",
            "adopt wrote the pre-lock adapter snapshot and dropped a concurrent field",
        )


class StopRealServicesTests(unittest.TestCase):
    """The cleanup helper requests the core stop and waits for release."""

    def _fixture(self, tmp: Path):
        engine = tmp / "kimi.engine.json"
        domain = tmp / "domain" / "vllm-ascend"
        domain.mkdir(parents=True)
        (domain / "consumer.lock").touch()
        engine.write_text(json.dumps({
            "root": str(tmp / "domain"),
            "domain": "vllm-ascend",
            "transcript_adapter": str(SCRIPTS / "transcript.py"),
            "redactor_executable": str(SCRIPTS / "transcript.py"),
        }))
        return engine, domain / "consumer.lock"

    def _patched_core(self, *, released_after):
        from unittest.mock import patch

        state = {"polls": 0, "stopped": 0, "sleeps": []}

        def rpc(connection, method, arguments=None, *, timeout=None):
            state["stopped"] += 1
            self.assertEqual(method, "stop")
            return {"status": "stopping"}

        def lock_held(path):
            state["polls"] += 1
            return False if state["polls"] > released_after else True

        def sleep(_seconds):
            state["sleeps"].append(1)

        return state, [
            patch("mindie_knowledge.loop.cli.connect", return_value={"url": "x", "token": "y"}),
            patch("mindie_knowledge.loop.cli.rpc", side_effect=rpc),
            patch("mindie_knowledge.loop.locks.lock_held", side_effect=lock_held),
        ], sleep

    def test_stop_waits_for_lock_release_within_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            engine, _consumer = self._fixture(Path(raw))
            state, patches, sleep = self._patched_core(released_after=3)
            for patcher in patches:
                patcher.start()
                self.addCleanup(patcher.stop)
            stop_real_services(engine, timeout=5.0, sleep=sleep)
            self.assertEqual(state["stopped"], 1)
            self.assertEqual(state["polls"], 4)
            self.assertEqual(len(state["sleeps"]), 3)

    def test_unconfirmed_stop_fails_loudly(self):
        with tempfile.TemporaryDirectory() as raw:
            engine, _consumer = self._fixture(Path(raw))
            from unittest.mock import patch

            with patch("mindie_knowledge.loop.cli.connect",
                       return_value={"url": "x", "token": "y"}), \
                 patch("mindie_knowledge.loop.cli.rpc",
                       side_effect=ConnectionError("gone")):
                with self.assertRaises(AssertionError):
                    stop_real_services(engine, timeout=1.0)

    def test_service_outliving_the_budget_fails_loudly(self):
        with tempfile.TemporaryDirectory() as raw:
            engine, _consumer = self._fixture(Path(raw))
            state, patches, sleep = self._patched_core(released_after=10**9)
            for patcher in patches:
                patcher.start()
                self.addCleanup(patcher.stop)
            with self.assertRaises(AssertionError):
                stop_real_services(engine, timeout=0.01, sleep=sleep)

    def test_no_endpoint_is_a_fast_noop(self):
        with tempfile.TemporaryDirectory() as raw:
            engine, _consumer = self._fixture(Path(raw))
            from unittest.mock import patch

            with patch("mindie_knowledge.loop.cli.connect",
                       side_effect=FileNotFoundError("no connection")) as connect:
                started = time.monotonic()
                stop_real_services(engine, timeout=5.0)
                self.assertLess(time.monotonic() - started, 1.0)
            connect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
