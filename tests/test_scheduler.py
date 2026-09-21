import json
import os
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import updater  # noqa: E402

SHA = "421e22bf1fbf524a074d84ae3e95c3323860becd"
STALE = "# stale bootstrap launcher\n"
CURRENT = "# current version launcher\n"


class SchedulerLauncherTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.config_path = make_config(self.tmp / "cfg")
        self.adapter = json.loads(self.config_path.read_text())
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config_path)
        self.gen = self.tmp / "generation"
        self.gen.mkdir()
        (self.gen / "scripts").mkdir()

    def tearDown(self):
        os.environ.pop("MINDIE_KIMI_CONFIG", None)
        self._tmp.cleanup()

    def write_current(self, sha):
        genstate.write_current(
            {
                "generation": str(self.gen),
                "python": sys.executable,
                "adapter_config": str(self.config_path),
                "sha": sha,
            },
            self.adapter,
        )

    def write_launch(self, identity, launcher_text, bounded=True):
        directory = genstate.launch_dir(self.adapter) / identity
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "mindie_launch.py").write_text(launcher_text)
        if bounded:
            (directory / "bounded.py").write_text("# bounded\n")
        return directory / "mindie_launch.py"

    def install(self):
        captured = []

        def fake_run(cmd, *_a, **_k):
            captured.append(list(cmd))
            return ""

        home = self.tmp / "fake-home"
        home.mkdir(exist_ok=True)
        with mock.patch.object(updater, "bounded_run", side_effect=fake_run), \
             mock.patch.object(updater.sys, "platform", "darwin"), \
             mock.patch("pathlib.Path.home", return_value=home):
            code = updater.install_schedule(self.config_path)
        plist = home / "Library" / "LaunchAgents" / "agent.mindie.kimi-update.plist"
        payload = plistlib.loads(plist.read_bytes()) if plist.is_file() else None
        return code, captured, payload

    def test_versioned_launcher_selected_over_stale_bootstrap(self):
        self.write_current(SHA)
        bootstrap = self.write_launch("bootstrap", STALE)
        current = self.write_launch(SHA, CURRENT)
        code, captured, payload = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(payload["ProgramArguments"][1], str(current))
        self.assertIn("--config", payload["ProgramArguments"])
        self.assertTrue(any("launchctl" in cmd[0] for cmd in captured))
        self.assertEqual(bootstrap.read_text(), STALE)

    def test_missing_current_launcher_does_not_use_bootstrap(self):
        self.write_current(SHA)
        bootstrap = self.write_launch("bootstrap", STALE)
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn(SHA, str(raised.exception))
        self.assertNotIn("bootstrap", str(raised.exception).split(SHA)[-1])
        self.assertEqual(bootstrap.read_text(), STALE)

    def test_fresh_sha_none_selects_bootstrap(self):
        self.write_current(None)
        launcher = self.write_launch("bootstrap", "# bootstrap\n")
        code, _captured, payload = self.install()
        self.assertEqual(code, 0)
        self.assertEqual(payload["ProgramArguments"][1], str(launcher))

    def test_missing_bounded_fails(self):
        self.write_current(SHA)
        self.write_launch(SHA, CURRENT, bounded=False)
        with self.assertRaises(SystemExit) as raised:
            self.install()
        self.assertIn("no launcher available", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
