import json
import os
import subprocess
import sys
import tempfile
import time
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
                    "--no-schedule",
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
                    "--no-schedule",
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

    def test_unconfigured_with_runtime_stays_on_this_python(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            missing = tmp / "absent.json"
            marker = tmp / "show.py"
            marker.write_text("import sys; print(sys.executable)\n")
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "with_runtime.py"), str(marker)],
                capture_output=True,
                text=True,
                timeout=5,
                env=env_for(extra={"MINDIE_KIMI_CONFIG": str(missing)}),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                Path(result.stdout.strip()).resolve(),
                Path(sys.executable).resolve(),
            )

    def test_requirements_are_pinned_commits(self):
        text = (SCRIPTS.parent / "runtime-requirements.txt").read_text()
        self.assertIn("3f7c70d813377d3ba3140a0a585f48e1df04444b", text)
        self.assertIn("13301ef7f52b53ffca0a6702a8a3c18f2edfcd52", text)
        self.assertNotIn("@main", text)

    def test_setup_bootstraps_generation_tuple_and_launcher(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "cfg" / "kimi.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "setup.py"),
                    "--knowledge-python",
                    sys.executable,
                    "--config",
                    str(config),
                    "--root",
                    str(tmp / "data"),
                    "--no-schedule",
                ],
                capture_output=True,
                text=True,
                timeout=20,
                env=env_for(),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            sys.path.insert(0, str(SCRIPTS))
            import genstate

            adapter = json.loads(config.read_text())
            self.assertIn("kimi_home", adapter)
            current = genstate.read_current(adapter)
            self.assertIsNone(current["sha"])
            self.assertEqual(current["adapter_config"], str(config))
            self.assertEqual(
                Path(current["python"]).resolve(),
                Path(sys.executable).resolve(),
            )
            launcher_dir = genstate.launch_dir(adapter) / "bootstrap"
            self.assertTrue((launcher_dir / "mindie_launch.py").is_file())
            self.assertTrue((launcher_dir / "bounded.py").is_file())
            payload = json.loads(result.stdout)
            package = Path(payload["native_package"])
            self.assertTrue((package / "kimi.plugin.json").is_file())
            manifest = json.loads((package / "kimi.plugin.json").read_text())
            self.assertTrue(manifest["version"].endswith("+mindie.bootstrap"))
            args = manifest["mcpServers"]["knowledge"]["args"]
            self.assertEqual(Path(args[0]), launcher_dir / "mindie_launch.py")

    def test_install_helper_timeout_leaves_no_web_child(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            pidfile = tmp / "child.pid"
            fake = tmp / "fake-kimi"
            # Local fake host: spawns a blocking child, records its PID,
            # then blocks forever (never a real host/model).
            fake.write_text(
                "#!/bin/sh\nsleep 600 &\necho $! > \"$FAKE_PIDFILE\"\nwait\n"
            )
            fake.chmod(0o755)
            env = env_for(extra={
                "MINDIE_KIMI_BIN": str(fake),
                "FAKE_PIDFILE": str(pidfile),
            })
            sys.path.insert(0, str(SCRIPTS))
            import bounded

            with self.assertRaises(RuntimeError):
                bounded.run(
                    [sys.executable,
                     str(SCRIPTS / "install_kimi_plugin.py"),
                     "--kimi-home", str(tmp / "home")],
                    "",
                    timeout=1.5,
                    env=env,
                )
            pid = int(pidfile.read_text().strip())
            alive = True
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except OSError:
                    alive = False
                    break
                time.sleep(0.1)
            self.assertFalse(alive, "fake web child outlived the helper")

    def test_install_helper_requires_explicit_home(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "install_kimi_plugin.py")],
            capture_output=True,
            text=True,
            timeout=5,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("kimi-home", result.stderr)
