"""Independent Kimi entry-boundary and concurrency contract tests.

These run under the same lane isolation and local model double as
`test_independent_contract.py`; the shared lane base is imported so both files
keep one fixture definition. Scratch stays under this lane.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from contract_support import pad_records as _pad_records
from contract_support import skill_origin as _skill_origin
from contract_support import user_record as _user_record
from support import (
    SCRIPTS,
    plugin_origin,
    stop_real_services,
    turn_records,
    write_session,
)
from test_independent_contract import SENTINEL, LaneIsolation, _reap, _write_consent


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
        for _round in range(8):
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
            except TimeoutError:
                raise
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
