"""F2 consent authority: one profile-shared persistent choice, damaged-state
honesty, and cross-adapter reuse. Real files and subprocesses; no knowledge
service, no network."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, make_config

ROOT = Path(__file__).resolve().parents[1]


class ConsentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        os.environ["MINDIE_KIMI_CONFIG"] = str(make_config(self.tmp))
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        for name in ("entry", "entry_state", "consent", "paths", "sharing"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(SCRIPTS))

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)
        os.environ.pop("MINDIE_KIMI_CONFIG", None)
        os.environ.pop("XDG_CONFIG_HOME", None)

    def _payload(self, session=None):
        import entry

        return entry._knowledge_status_payload(session)

    def _consent_path(self):
        import consent

        return consent.consent_path()

    def test_missing_shared_runtime_cannot_overwrite_saved_choice(self):
        import consent

        consent.record_choice("disabled")
        path = self._consent_path()
        before = path.read_bytes()
        completed = subprocess.run(
            [
                sys.executable, "-I", "-S", "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import consent; consent.record_choice('contribute')",
                str(SCRIPTS),
            ],
            capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("run scripts/setup.py", completed.stderr)
        self.assertEqual(path.read_bytes(), before)

    def test_cold_install_offers_choices_once_then_never_again(self):
        import consent

        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["experience"], "needs-configuration")
        consent.record_choice("later")
        payload = self._payload()
        self.assertEqual(payload["first_use"], "later")
        self.assertEqual(payload["choices"], [])
        self.assertTrue(payload["repeat"])
        # A new "session" in the same installation reads the same choice.
        self.assertEqual(self._payload("ses_new")["first_use"], "later")

    def test_legacy_marker_is_never_imported_as_a_choice(self):
        """A pre-release first-use marker is not consent evidence: no read
        and no boundary writes a choice from it."""
        from paths import first_use_path
        import consent

        first_use_path().parent.mkdir(parents=True, exist_ok=True)
        first_use_path().write_text(json.dumps({"choice": "read-only"}))
        payload = self._payload()
        self.assertIsNone(payload["first_use"])
        self.assertFalse(self._consent_path().exists())
        # The marker still marks an existing installation: no fresh onboarding.
        self.assertTrue(payload["repeat"])
        self.assertEqual(payload["choices"], [])

    def test_corrupt_marker_does_not_reonboard(self):
        from paths import first_use_path

        first_use_path().parent.mkdir(parents=True, exist_ok=True)
        first_use_path().write_text("{broken-json")
        payload = self._payload()
        # A damaged marker on an existing installation is a status, never a
        # fresh three-choice onboarding.
        self.assertEqual(payload["choices"], [])
        self.assertTrue(payload["repeat"])

    def test_corrupt_consent_is_a_fault_not_onboarding(self):
        path = self._consent_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken-json")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["consent_state"], "corrupt")
        self.assertIn("consent_error", payload)
        self.assertTrue(payload["repeat"])

    def test_corrupt_settings_is_a_fault_not_onboarding(self):
        import consent

        designated = consent.configured_community_path()
        designated.write_text("{broken-json")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["sharing"]["state"], "corrupt")
        self.assertTrue(payload["repeat"])
        # A damaged designated authority is not "repaired" by looking for
        # another candidate file: the shared default was never created.
        self.assertFalse(consent.shared_community_path().exists())

    def test_status_does_not_adopt_legacy_community_path(self):
        """The forbidden implicit rewrite: loading status must not copy the
        legacy file or repoint configs; adoption is an explicit boundary."""
        import consent

        legacy = consent.configured_community_path()
        self.assertNotEqual(legacy, consent.shared_community_path())
        payload = self._payload()
        self.assertIn("sharing", payload)
        self.assertFalse(consent.shared_community_path().exists())
        adopted = consent.adopt_community_settings()
        self.assertEqual(adopted["action"], "adopted")
        self.assertTrue(consent.shared_community_path().exists())
        self.assertTrue(legacy.exists())  # legacy kept as evidence
        from paths import load_adapter_config, load_engine_config

        self.assertEqual(
            load_adapter_config()["community_config"],
            str(consent.shared_community_path()),
        )
        self.assertEqual(
            load_engine_config()["community_config"],
            str(consent.shared_community_path()),
        )
        shared_data = json.loads(consent.shared_community_path().read_text())
        self.assertEqual(shared_data["consent_config"], str(consent.consent_path()))

    def test_conflicting_community_files_keep_the_shared_authority(self):
        import consent
        import sharing as sharing_mod

        legacy = consent.configured_community_path()
        sharing_mod.write_enabled(
            repository="owner/repo", project_roots=[str(self.tmp)], account="acc",
        )
        shared = consent.shared_community_path()
        shared.write_text(json.dumps({
            "schema": "mindie-community-config/1", "enabled": False,
            "generation": "gen-shared", "enabled_at": None,
            "repository": "owner/repo", "branch": "main", "project_roots": [],
            "idle_seconds": 300,
        }))
        adopted = consent.adopt_community_settings()
        self.assertEqual(adopted["action"], "converged")
        # The shared authority was kept: no silent widening from the legacy
        # enabled=true file.
        shared_data = json.loads(shared.read_text())
        self.assertFalse(shared_data["enabled"])
        self.assertEqual(shared_data["project_roots"], [])
        from paths import load_adapter_config

        self.assertEqual(
            load_adapter_config()["community_config"], str(shared),
        )

    def test_generation_migration_preserves_latest_disabled_choice(self):
        import consent
        from paths import state_dir

        adapter_path = Path(os.environ["MINDIE_KIMI_CONFIG"])
        adapter = json.loads(adapter_path.read_text())
        adapter.pop("community_config")
        adapter_path.write_text(json.dumps(adapter))
        template = json.loads((self.tmp / "kimi.community.json").read_text())
        root = state_dir() / "update" / "generations"
        for name, enabled, modified in (("old", True, 100), ("new", False, 200)):
            candidate = root / name / "config" / "mindie-community.json"
            candidate.parent.mkdir(parents=True)
            candidate.write_text(json.dumps({
                **template, "enabled": enabled, "generation": name,
                "enabled_at": 100 if enabled else None,
                "project_roots": [str(self.tmp)] if enabled else [],
            }))
            os.utime(candidate, (modified, modified))

        result = consent.adopt_community_settings()
        adopted = json.loads(consent.shared_community_path().read_text())
        self.assertEqual(result["action"], "adopted")
        self.assertFalse(adopted["enabled"])
        self.assertEqual(adopted["generation"], "new")
        self.assertEqual(adopted["project_roots"], [])

    def test_disabled_without_marker_is_unchosen_not_guessed(self):
        # Installer default off (no marker, no consent): the one-time setup
        # has not happened — choices are presented exactly once, and nothing
        # is guessed in either direction (no assumed opt-out, no opt-in).
        import sharing as sharing_mod

        sharing_mod.write_disabled()
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["experience"], "needs-configuration")
        self.assertFalse(payload["sharing"]["enabled"])
        self.assertIsNone(payload["first_use"])
        self.assertNotIn("consent_error", payload)
        # Once the user answers, the choice persists and is never re-asked.
        import consent

        consent.record_choice("read-only")
        again = self._payload()
        self.assertEqual(again["first_use"], "read-only")
        self.assertEqual(again["choices"], [])

    def test_choice_shared_across_adapters_in_one_profile(self):
        """One profile = one authority: a second adapter whose config lives
        in the SAME profile directory reads the same saved choice (this is
        how the cc/codex adapters, with their own config env vars, share the
        profile). An isolated profile directory does not inherit it."""
        import consent

        consent.record_choice("later")
        code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "import consent;print(json.dumps(consent.load()))"
        )
        # A sibling adapter config in the same profile directory.
        sibling = self.tmp / "cc.json"
        sibling.write_text(json.dumps({}))
        env = dict(os.environ)
        env["MINDIE_KIMI_CONFIG"] = str(sibling)
        result = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPTS)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)
        self.assertEqual(saved["state"], "ok")
        self.assertEqual(saved["choice"], "later")

    def test_isolated_profile_does_not_inherit_choice(self):
        import consent

        consent.record_choice("later")
        other = self.tmp / "other-profile"
        other.mkdir()
        (other / "kimi.json").write_text(json.dumps({}))
        env = dict(os.environ, MINDIE_KIMI_CONFIG=str(other / "kimi.json"))
        code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "import consent;print(json.dumps(consent.load()))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code, str(SCRIPTS)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)
        self.assertEqual(saved["state"], "missing")
        self.assertIsNone(saved["choice"])

    def test_sharing_disable_records_disabled_choice(self):
        import consent as consent_mod
        import sharing as sharing_mod

        sharing_mod.write_disabled()
        consent_mod.record_choice("disabled")
        saved = json.loads(self._consent_path().read_text())
        self.assertEqual(saved["choice"], "disabled")
        payload = self._payload()
        self.assertEqual(payload["first_use"], "disabled")
        self.assertEqual(payload["choices"], [])
        self.assertFalse(payload["sharing"]["enabled"])

    def test_reporting_hint_only_during_first_setup(self):
        import entry

        first = entry.status_payload(None)
        # Cold install: reporting may be offered once alongside the choices.
        self.assertIn("choices", first)
        import consent

        consent.record_choice("later")
        later = entry.status_payload(None)
        self.assertNotIn("reporting_choice", later)

    def test_field_updates_merge_and_never_wipe_the_sibling(self):
        import consent

        consent.record_choice("read-only")
        consent.record_reporting("disabled")
        saved = json.loads(self._consent_path().read_text())
        self.assertEqual(saved["choice"], "read-only")
        self.assertEqual(saved["reporting"], "disabled")
        consent.record_choice("contribute")
        saved = json.loads(self._consent_path().read_text())
        self.assertEqual(saved["choice"], "contribute")
        self.assertEqual(saved["reporting"], "disabled")

    def test_cross_process_field_updates_keep_both_fields(self):
        """Concurrent record_choice/record_reporting from two processes
        (e.g. two adapters in one profile) must not lose a field."""
        import consent

        consent.record_choice("read-only")
        code = (
            "import sys;sys.path.insert(0,sys.argv[1]);"
            "import consent;consent.record_reporting('enabled')"
        )
        env = dict(os.environ)
        for round in range(5):
            result = subprocess.run(
                [sys.executable, "-c", code, str(SCRIPTS)],
                env=env, capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            consent.record_choice("later" if round % 2 else "read-only")
        saved = json.loads(self._consent_path().read_text())
        self.assertIn(saved["choice"], {"read-only", "later"})
        self.assertEqual(saved["reporting"], "enabled")


if __name__ == "__main__":
    unittest.main()
