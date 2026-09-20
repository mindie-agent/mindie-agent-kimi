import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, env_for


class SetupInstallTests(unittest.TestCase):
    def test_setup_default_sharing_off_and_no_session_activation_alias(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "cfg" / "kimi.json"
            root = tmp / "data"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "setup.py"),
                    "--knowledge-python",
                    sys.executable,
                    "--config",
                    str(config),
                    "--root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=env_for(),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["sharing"], "off")
            engine = json.loads(Path(payload["engine_config"]).read_text())
            self.assertNotIn("session_activation", engine)
            self.assertTrue(engine["admission_path"].endswith("admission.sqlite3"))

    def test_setup_configures_sharing_after_install(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "kimi.json"
            project = tmp / "proj"
            project.mkdir()
            env = env_for()
            first = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "setup.py"),
                    "--knowledge-python",
                    sys.executable,
                    "--config",
                    str(config),
                    "--root",
                    str(tmp / "data"),
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "setup.py"),
                    "--knowledge-python",
                    sys.executable,
                    "--config",
                    str(config),
                    "--root",
                    str(tmp / "data"),
                    "--community-repository",
                    "owner/repo",
                    "--community-project-root",
                    str(project),
                    "--community-visibility",
                    "public",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=env,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            payload = json.loads(second.stdout)
            self.assertEqual(payload["sharing"], "enabled")

    def test_install_helper_requires_explicit_home(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "install_kimi_plugin.py")],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kimi-home", result.stderr)
