import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import SCRIPTS, env_for


class SetupInstallTests(unittest.TestCase):
    def test_setup_stamps_kimi_bin_and_service_launch_context(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            fake_home = tmp / "kimi-home"
            (fake_home / "bin").mkdir(parents=True)
            binary = fake_home / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")
            binary.write_text("# native binary\n")
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
                    "--kimi-home",
                    str(fake_home),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                env=env_for(),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["kimi_bin"], str(binary))
            self.assertNotIn("warnings", payload)
            adapter = json.loads(config.read_text())
            self.assertEqual(adapter["kimi_bin"], str(binary))
            import shutil as shutil_mod

            git = shutil_mod.which("git")
            self.assertIsNotNone(git, "test requires git on PATH")
            self.assertEqual(payload["git_bin"], git)
            self.assertEqual(adapter["git_bin"], git)
            engine = json.loads(Path(payload["engine_config"]).read_text())
            self.assertEqual(
                engine["service_launch_context"],
                {"paths": {}, "environment": ["MINDIE_KIMI_BIN", "MINDIE_GIT_BIN", "MINDIE_DIAGNOSTICS_CONFIG"]},
            )
            from mindie_knowledge.loop import service_context

            self.assertEqual(
                service_context.validate(engine),
                {"paths": {}, "environment": ["MINDIE_KIMI_BIN", "MINDIE_GIT_BIN", "MINDIE_DIAGNOSTICS_CONFIG"]},
            )

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
                timeout=90,
                env=env_for(),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["sharing"], "off")
            engine = json.loads(Path(payload["engine_config"]).read_text())
            self.assertNotIn("session_activation", engine)
            self.assertTrue(engine["admission_path"].endswith("admission.sqlite3"))
            community = json.loads(config.with_name("mindie-community.json").read_text())
            self.assertFalse(community["enabled"])
            self.assertIsNone(community["repository"])
            adapter = json.loads(config.read_text())
            self.assertEqual(set(adapter["domain_configs"]), {"vllm-ascend", "operators"})
            self.assertEqual(adapter["domain_configs"]["vllm-ascend"], adapter["engine_config"])
            self.assertEqual(engine["feeds"][0]["repository"],
                             "mindie-agent/knowledge-vllm-ascend")
            self.assertEqual(engine["feeds"][0]["ref"], "main")
            route = json.loads(Path(adapter["domain_configs"]["operators"]).read_text())
            self.assertEqual(route["feeds"][0]["repository"],
                             "mindie-agent/knowledge-operators")
            self.assertEqual(route["feeds"][0]["ref"], "main")
            settings = json.loads(Path(route["community_config"]).read_text())
            self.assertEqual(settings["repository"], "mindie-agent/knowledge-operators")
            self.assertEqual(settings["branch"], "main")
            self.assertFalse(settings["enabled"])
            self.assertEqual(settings["consent_config"],
                             str(config.with_name("mindie-consent.json")))

    def test_setup_operators_root_uses_operators_main_feed_and_vllm_secondary(self):
        import contextlib
        import io
        from unittest.mock import patch

        if str(SCRIPTS) not in sys.path:
            sys.path.insert(0, str(SCRIPTS))
        import capture_config
        import setup

        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "cfg" / "kimi.json"
            capture = {
                "capture_mode": "public-transcript",
                "redactor_executable": sys.executable,
                "summary_command": [sys.executable, str(SCRIPTS / "organizer.py")],
                "service_launch_context": {"paths": {}, "environment": []},
            }
            with patch.object(capture_config, "prepare", return_value=capture), \
                    patch.object(sys, "argv", [
                        "setup.py",
                        "--knowledge-python", sys.executable,
                        "--config", str(config),
                        "--root", str(tmp / "data"),
                        "--domain", "operators",
                        "--no-schedule",
                        "--kimi-home", str(tmp / "kimi-home"),
                    ]), \
                    contextlib.redirect_stdout(io.StringIO()):
                setup.main()
            adapter = json.loads(config.read_text())
            engine = json.loads(Path(adapter["engine_config"]).read_text())
            self.assertEqual(engine["domain"], "operators")
            self.assertEqual(engine["feeds"], [{
                "repository": "mindie-agent/knowledge-operators",
                "ref": "main", "domain": "operators", "interval_seconds": 300,
            }])
            routes = adapter["domain_configs"]
            self.assertEqual(set(routes), {"operators", "vllm-ascend"})
            self.assertEqual(routes["operators"], adapter["engine_config"])
            secondary = json.loads(Path(routes["vllm-ascend"]).read_text())
            self.assertEqual(secondary["feeds"], [{
                "repository": "mindie-agent/knowledge-vllm-ascend",
                "ref": "main", "domain": "vllm-ascend", "interval_seconds": 300,
            }])
            consent = str(config.with_name("mindie-consent.json"))
            for route in (engine, secondary):
                settings = json.loads(Path(route["community_config"]).read_text())
                self.assertFalse(settings["enabled"])
                self.assertEqual(settings["consent_config"], consent)

    def test_setup_configures_sharing_after_install(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "kimi.json"
            project = tmp / "proj"
            project.mkdir()
            env = env_for(extra={"MINDIE_DIAGNOSTICS_ROOT": str(tmp / "diag-root"),
                                 "MINDIE_DIAGNOSTICS_CONFIG": str(tmp / "diagnostics.json")})
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
                timeout=90,
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
                    "--no-schedule",
                ],
                capture_output=True,
                text=True,
                timeout=90,
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

    def test_bootstrap_uses_system_trust_and_repairs_an_interrupted_install(self):
        sys.path.insert(0, str(SCRIPTS))
        import setup
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            python = root / "bootstrap-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            calls = []
            def run(argv, stdin, **options):
                calls.append((argv, options))
                if argv[1] == "venv":
                    python.parent.mkdir(parents=True)
                    python.touch()
                elif len(calls) == 2:
                    raise RuntimeError("download interrupted")
                return ""
            with patch.object(setup.shutil, "which", return_value="uv"), \
                    patch.object(setup, "run", side_effect=run), \
                    patch.dict(os.environ, {"UV_NATIVE_TLS": "true", "SSL_CERT_FILE": "company.pem"}):
                with self.assertRaisesRegex(RuntimeError, "download interrupted"):
                    setup.build_bootstrap_runtime(root)
                self.assertEqual(setup.build_bootstrap_runtime(root), str(python))
            self.assertEqual([argv[1] for argv, _ in calls], ["venv", "pip", "pip"])
            for argv, options in calls:
                self.assertEqual(options["env"]["UV_NATIVE_TLS"], "true")
                self.assertEqual(options["env"]["SSL_CERT_FILE"], "company.pem")
                self.assertEqual(options["env"]["GIT_CONFIG_COUNT"], "3")
                self.assertEqual(options["env"]["GIT_CONFIG_KEY_0"], "core.longpaths")
                self.assertEqual(options["env"]["GIT_CONFIG_VALUE_0"], "true")

    def test_bootstrap_without_uv_fails_before_creating_runtime(self):
        sys.path.insert(0, str(SCRIPTS))
        import setup
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as raw:
            with patch.object(setup.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "uv is required"):
                    setup.build_bootstrap_runtime(Path(raw))
            self.assertFalse((Path(raw) / "bootstrap-venv").exists())

    def test_requirements_are_pinned_commits(self):
        expected = {
            "mindie-diagnostics": "https://github.com/mindie-agent/diagnostics",
            "mindie-knowledge": "https://github.com/mindie-agent/knowledge",
            "remote-dev": "https://github.com/mindie-agent/remote-dev",
        }
        pattern = re.compile(
            r"(?P<name>[^\s]+) @ git\+(?P<url>https://github.com/[^@]+)@(?P<sha>[0-9a-f]{40})$"
        )
        lines = (SCRIPTS.parent / "runtime-requirements.txt").read_text().splitlines()
        actual = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            match = pattern.fullmatch(stripped)
            self.assertIsNotNone(
                match,
                f"requirement must be NAME @ git+https://github.com/ORG/REPO@40hex: {stripped!r}",
            )
            actual.append((match.group("name"), match.group("url")))
        self.assertEqual(dict(actual), expected)
        self.assertEqual(len(actual), len(expected))

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
                timeout=90,
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
            generation = Path(current["generation"])
            self.assertNotEqual(generation.resolve(), (SCRIPTS.parent).resolve())
            self.assertTrue((generation / "scripts" / "transcript.py").is_file())
            self.assertTrue((generation / "scripts" / "organizer.py").is_file())
            self.assertTrue((generation / "scripts" / "organizer_distill.py").is_file())
            engine = json.loads(Path(adapter["engine_config"]).read_text())
            self.assertEqual(engine["transcript_adapter"],
                             str(generation / "scripts" / "transcript.py"))
            self.assertNotIn("agent_command", engine)
            self.assertEqual(engine["capture_mode"], "public-transcript")
            self.assertEqual(engine["summary_command"][1],
                             str(generation / "scripts" / "organizer.py"))
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
            self.assertEqual(args[1:4], ["--config", str(config), "mcp"])
            command = manifest["mcpServers"]["knowledge"]["command"]
            if os.name == "nt":
                self.assertEqual(command, Path(sys.executable).name)
            else:
                self.assertTrue(command.startswith("./"), command)
                self.assertTrue((package / command[2:]).is_file())
            hook = next(h for h in manifest["hooks"] if h.get("event") == "Stop")
            self.assertIn("--config", hook["command"])
            self.assertIn(str(config), hook["command"])

    @unittest.skipUnless(os.name == "posix", "POSIX shell host; native Windows tree has separate real coverage")
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
                    # Generous ceiling: the deadline must fire after the fake
                    # host started even under full-suite load; the tree-kill
                    # assertion below is what this test proves.
                    timeout=10,
                    env=env,
                )
            pid = int(pidfile.read_text().strip())
            alive = True
            for _ in range(30):
                try:
                    os.kill(pid, 0)
                except TimeoutError:
                    raise
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

    def test_install_helper_rejects_ok_record_with_zero_mcp_servers(self):
        sys.path.insert(0, str(SCRIPTS))
        import install_kimi_plugin

        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            (package / "kimi.plugin.json").write_text(
                json.dumps({
                    "name": "mindie-agent",
                    "version": "0.1.0+mindie.bootstrap",
                    "mcpServers": {
                        "knowledge": {"command": "./mindie-front", "args": []},
                        "remote": {"command": "./mindie-front", "args": []},
                    },
                    "hooks": [{"event": "Stop", "command": "python3 x", "timeout": 2}],
                })
            )
            skipped = {
                "id": "mindie-agent",
                "version": "0.1.0+mindie.bootstrap",
                "enabled": True,
                "state": "ok",
                "hasErrors": False,
                "mcpServerCount": 0,
                "enabledMcpServerCount": 0,
                "hookCount": 2,
                "commandCount": 7,
                "mcpServers": [],
                "diagnostics": [{
                    "severity": "warn",
                    "message": '"mcpServers.knowledge.command" must be a PATH command or start with "./"',
                }],
            }
            report = {
                "install": {"body": {"data": skipped}},
                "after": {"body": {"data": {"plugins": [dict(skipped)]}}},
            }
            with self.assertRaises(SystemExit):
                install_kimi_plugin.verify_install_report(
                    report, package,
                    json.loads((package / "kimi.plugin.json").read_text()),
                )

    def test_install_helper_rejects_missing_after_inventory(self):
        sys.path.insert(0, str(SCRIPTS))
        import install_kimi_plugin

        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            version = "0.1.0+mindie.bootstrap"
            (package / "kimi.plugin.json").write_text(
                json.dumps({
                    "name": "mindie-agent",
                    "version": version,
                    "mcpServers": {
                        "knowledge": {"command": "./mindie-front", "args": []},
                        "remote": {"command": "./mindie-front", "args": []},
                    },
                    "hooks": [{"event": "Stop", "command": "python3 x", "timeout": 2}],
                })
            )
            ok = {
                "id": "mindie-agent",
                "version": version,
                "enabled": True,
                "state": "ok",
                "hasErrors": False,
                "originalSource": str(package),
                "mcpServerCount": 2,
                "enabledMcpServerCount": 2,
                "hookCount": 1,
                "commandCount": 0,
                "mcpServers": [
                    {"name": "knowledge", "enabled": True},
                    {"name": "remote", "enabled": True},
                ],
                "diagnostics": [],
            }
            manifest = json.loads((package / "kimi.plugin.json").read_text())
            missing = {
                "install": {"body": {"data": dict(ok)}},
                "after": {"body": {"data": {}}},
            }
            with self.assertRaises(SystemExit) as raised:
                install_kimi_plugin.verify_install_report(missing, package, manifest)
            self.assertIn("after inventory", str(raised.exception))
            masked = dict(ok, version="other", enabled=False)
            counts_ok = {
                "install": {"body": {"data": dict(ok)}},
                "after": {"body": {"data": {"plugins": [masked]}}},
            }
            with self.assertRaises(SystemExit):
                install_kimi_plugin.verify_install_report(counts_ok, package, manifest)

    def test_retained_bootstrap_survives_source_change(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "cfg" / "kimi.json"
            source_root = tmp / "source"
            shutil.copytree(SCRIPTS.parent, source_root,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            first = subprocess.run(
                [
                    sys.executable,
                    str(source_root / "scripts" / "setup.py"),
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
                timeout=90,
                env=env_for(),
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            sys.path.insert(0, str(SCRIPTS))
            import genstate

            adapter = json.loads(config.read_text())
            generation = Path(genstate.read_current(adapter)["generation"])
            retained = generation / "scripts" / "organizer.py"
            source = source_root / "scripts" / "organizer.py"
            original = source.read_text()
            before = retained.read_text()
            try:
                source.write_text(original + "\n# mutated-source-checkout\n")
                self.assertEqual(retained.read_text(), before)
                self.assertNotIn("mutated-source-checkout", retained.read_text())
            finally:
                source.write_text(original)

    def test_sharing_setup_does_not_rewrite_live_bootstrap(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = tmp / "kimi.json"
            project = tmp / "proj"
            project.mkdir()
            env = env_for(extra={"MINDIE_DIAGNOSTICS_ROOT": str(tmp / "diag-root"),
                                 "MINDIE_DIAGNOSTICS_CONFIG": str(tmp / "diagnostics.json")})
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
                timeout=90,
                env=env,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            sys.path.insert(0, str(SCRIPTS))
            import genstate

            adapter = json.loads(config.read_text())
            generation = Path(genstate.read_current(adapter)["generation"])
            launcher = genstate.launch_dir(adapter) / "bootstrap" / "mindie_launch.py"
            gen_stat = (generation / "scripts" / "organizer.py").stat()
            launch_bytes = launcher.read_bytes()
            launch_stat = launcher.stat()
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
                    "--no-schedule",
                ],
                capture_output=True,
                text=True,
                timeout=90,
                env=env,
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            after_gen = (generation / "scripts" / "organizer.py").stat()
            self.assertEqual(after_gen.st_mtime_ns, gen_stat.st_mtime_ns)
            self.assertEqual(launcher.read_bytes(), launch_bytes)
            self.assertEqual(launcher.stat().st_mtime_ns, launch_stat.st_mtime_ns)
            self.assertEqual(
                Path(genstate.read_current(adapter)["generation"]),
                generation,
            )

    def test_retained_bootstrap_deploys_the_c6_worker_helper(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            source = tmp / "source"
            (source / "scripts").mkdir(parents=True)
            (source / "scripts" / "organizer.py").write_text("# organizer\n")
            (source / "scripts" / "organizer_distill.py").write_text("# c6 helper\n")
            (source / "skills").mkdir()
            (source / "commands").mkdir()
            (source / "kimi.plugin.json").write_text("{}\n")
            (source / "runtime-requirements.txt").write_text("\n")
            (source / "runtime").mkdir()
            (source / "runtime/active-overrides.txt").write_text(
                (SCRIPTS.parent / "runtime/active-overrides.txt").read_text())
            sys.path.insert(0, str(SCRIPTS))
            import setup

            dest = setup.stage_retained_bootstrap(tmp / "bootstrap", source)
            self.assertEqual(
                (dest / "scripts" / "organizer_distill.py").read_text(),
                "# c6 helper\n",
            )
            self.assertEqual(
                (dest / "runtime/active-overrides.txt").read_text(),
                (source / "runtime/active-overrides.txt").read_text(),
            )

    def test_partial_retained_bootstrap_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            dest = tmp / "bootstrap"
            dest.mkdir()
            scripts = dest / "scripts"
            scripts.mkdir()
            (scripts / "transcript.py").write_text("# partial\n")
            (scripts / "organizer.py").write_text("# partial\n")
            sys.path.insert(0, str(SCRIPTS))
            import setup

            with self.assertRaises(SystemExit) as raised:
                setup.stage_retained_bootstrap(dest, setup.PLUGIN_ROOT)
            self.assertIn("incomplete or unknown retained bootstrap", str(raised.exception))
            self.assertEqual((scripts / "transcript.py").read_text(), "# partial\n")
            self.assertFalse((dest / "commands").exists())
            self.assertFalse((dest / "kimi.plugin.json").exists())
            self.assertFalse((dest / setup.BOOTSTRAP_COMPLETE).exists())
            leftovers = [p.name for p in dest.parent.iterdir() if p.name != dest.name]
            self.assertEqual(leftovers, [])
