"""F2 consent authority: one profile-shared persistent choice, legacy
migration, damaged-state honesty, and cross-adapter reuse. Real files and
subprocesses; no knowledge service, no network."""

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

    def test_cold_install_offers_choices_once_then_never_again(self):
        import entry_state

        payload = self._payload()
        self.assertEqual(len(payload["choices"]), 3)
        entry_state.set_first_use("later")
        payload = self._payload()
        self.assertEqual(payload["first_use"], "later")
        self.assertEqual(payload["choices"], [])
        self.assertTrue(payload["repeat"])
        # A new "session" in the same installation reads the same choice.
        self.assertEqual(self._payload("ses_new")["first_use"], "later")

    def test_legacy_marker_choice_migrates_once(self):
        from paths import first_use_path

        first_use_path().parent.mkdir(parents=True, exist_ok=True)
        first_use_path().write_text(json.dumps({"choice": "read-only"}))
        payload = self._payload()
        self.assertEqual(payload["first_use"], "read-only")
        self.assertEqual(payload["choices"], [])
        saved = json.loads(self._consent_path().read_text())
        self.assertEqual(saved["choice"], "read-only")
        # The marker is then ignored: deleting it changes nothing.
        first_use_path().unlink()
        self.assertEqual(self._payload()["first_use"], "read-only")

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

        legacy = consent.resolve_community_path()
        legacy.write_text("{broken-json")
        payload = self._payload()
        self.assertEqual(payload["choices"], [])
        self.assertEqual(payload["sharing"]["state"], "corrupt")
        self.assertTrue(payload["repeat"])

    def test_disabled_without_marker_is_unchosen_not_guessed(self):
        # Installer default off (no marker, no consent): the one-time setup
        # has not happened — choices are presented exactly once, and nothing
        # is guessed in either direction (no assumed opt-out, no opt-in).
        import sharing as sharing_mod

        sharing_mod.write_disabled()
        payload = self._payload()
        self.assertEqual(len(payload["choices"]), 3)
        self.assertFalse(payload["sharing"]["enabled"])
        self.assertIsNone(payload["first_use"])
        self.assertNotIn("consent_error", payload)
        # Once the user answers, the choice persists and is never re-asked.
        import entry_state

        entry_state.set_first_use("read-only")
        again = self._payload()
        self.assertEqual(again["first_use"], "read-only")
        self.assertEqual(again["choices"], [])

    def test_choice_shared_across_adapters_in_one_profile(self):
        import entry_state

        entry_state.set_first_use("later")
        code = (
            "import sys,json;sys.path.insert(0,sys.argv[1]);"
            "import consent;print(json.dumps(consent.load()))"
        )
        cc_scripts = ROOT.parent / "cc" / "scripts"
        env = dict(os.environ)
        env.pop("MINDIE_KIMI_CONFIG", None)
        env["MINDIE_CC_CONFIG"] = os.environ.get("MINDIE_CC_CONFIG") or str(
            self.tmp / "cc.json"
        )
        # cc's adapter config is a sibling of kimi's in this profile.
        (self.tmp / "cc.json").write_text(json.dumps({}))
        result = subprocess.run(
            [sys.executable, "-c", code, str(cc_scripts)],
            env=env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        saved = json.loads(result.stdout)
        self.assertEqual(saved["state"], "ok")
        self.assertEqual(saved["choice"], "later")

    def test_isolated_profile_does_not_inherit_choice(self):
        import entry_state

        entry_state.set_first_use("later")
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
        import entry_state

        entry_state.set_first_use("later")
        later = entry.status_payload(None)
        self.assertNotIn("reporting_choice", later)


if __name__ == "__main__":
    unittest.main()
