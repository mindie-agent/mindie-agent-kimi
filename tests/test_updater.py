import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import SCRIPTS, make_config, write_json

sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import updater  # noqa: E402

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
)

_MAJOR_MINOR, _PATCH = json.loads((SCRIPTS.parent / "kimi.plugin.json").read_text())["version"].rsplit(".", 1)
MANIFEST = {
    "name": "mindie-agent",
    "version": f"{_MAJOR_MINOR}.{int(_PATCH) + 1}",
    "mcpServers": {
        "knowledge": {"command": "python3", "args": ["./old"]},
        "remote": {"command": "python3", "args": ["./old"]},
    },
    "hooks": [
        {"event": "PreToolUse", "matcher": "mcp__plugin-mindie-agent_",
         "command": "python3 ./old pretool", "timeout": 2},
        {"event": "Stop", "command": "python3 ./old stop", "timeout": 2},
    ],
}


def git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   text=True, env=GIT_ENV)


def commit(src, bare, marker):
    manifest = json.loads((src / "kimi.plugin.json").read_text())
    major, minor, patch_version = manifest["version"].split(".")
    manifest["version"] = f"{major}.{minor}.{int(patch_version) + 1}"
    write_json(src / "kimi.plugin.json", manifest)
    (src / "marker.txt").write_text(marker)
    git(["add", "-A"], src)
    git(["commit", "-m", marker], src)
    git(["push", str(bare), "main"], src)
    publish_release(src, bare)


def publish_release(src, bare):
    from release_metadata import metadata

    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True,
                         capture_output=True, text=True, env=GIT_ENV).stdout.strip()
    declaration = metadata(src, sha)
    tag = "v" + declaration["version"]
    git(["tag", tag], src)
    git(["push", str(bare), tag], src)
    catalog_path = bare.parent / "releases.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else []
    catalog.append(dict(
        id=len(catalog) + 1, tag_name=tag, draft=False, prerelease=False,
        metadata=declaration,
    ))
    write_json(catalog_path, catalog)


def local_release_catalog(adapter, current, deadline):
    from mindie_knowledge.releases import select_release
    from release_metadata import HARNESS, REPOSITORY

    catalog = json.loads((Path(adapter["update_remote"]).parent / "releases.json").read_text())
    return select_release(
        catalog, repository=REPOSITORY, harness_name=HARNESS,
        harness_version="2.2.0", installed_version=updater._installed_version(current),
    )


def make_remote(tmp: Path):
    src = tmp / "remote-src"
    bare = tmp / "remote.git"
    (src / "scripts").mkdir(parents=True)
    (src / "skills").mkdir()
    (src / "commands").mkdir()
    write_json(src / "kimi.plugin.json", MANIFEST)
    write_json(src / "harness-compatibility.json",
               json.loads((SCRIPTS.parent / "harness-compatibility.json").read_text()))
    (src / "runtime-requirements.txt").write_text(
        "mindie-knowledge @ git+https://github.com/mindie-agent/knowledge"
        "@3f7c70d813377d3ba3140a0a585f48e1df04444b\n"
    )
    (src / "scripts" / "mindie_launch.py").write_text("# stable launcher\n")
    (src / "scripts" / "bounded.py").write_text("# bounded dependency\n")
    (src / "scripts" / "transcript.py").write_text("# parser\n")
    (src / "scripts" / "organizer.py").write_text("# organizer\n")
    # Production generations are full checkouts; carry the real service
    # modules so post-switch idle probes exercise the actual interface.
    import shutil

    for name in ("knowledge_service.py", "paths.py", "transcript.py",
                 "diagnostic_support.py", "paths.py"):
        shutil.copy2(SCRIPTS / name, src / "scripts" / name)
    (src / "marker.txt").write_text("one")
    git(["init", "-b", "main"], src)
    git(["add", "-A"], src)
    git(["commit", "-m", "one"], src)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True,
                         capture_output=True, text=True, env=GIT_ENV).stdout.strip()
    git(["init", "--bare", str(bare)], tmp)
    git(["push", str(bare), "main"], src)
    publish_release(src, bare)
    return src, bare, sha


def fake_build(generation: Path, deadline: float) -> Path:
    # Only dependency installation is replaced. Probe, idle handshake,
    # manifest and transaction paths still execute the real pinned runtime.
    return Path(sys.executable)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        component_prepare = patch("component_update.prepare", return_value=None)
        component_check = patch("component_update.check", return_value={"status": "up_to_date"})
        self.component_prepare = component_prepare.start()
        self.component_check = component_check.start()
        self.addCleanup(component_prepare.stop)
        self.addCleanup(component_check.stop)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp
        self.src, self.bare, self.sha = make_remote(tmp)
        self.config_path = make_config(tmp / "cfg", sharing=True,
                                       roots=[tmp / "cfg"])
        data = json.loads(self.config_path.read_text())
        data["update_remote"] = str(self.bare)
        data["kimi_home"] = str(tmp / "kimi-home")
        write_json(self.config_path, data)
        self.adapter = data
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config_path)
        bootstrap = genstate.launch_dir(self.adapter) / "bootstrap" / "mindie_launch.py"
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        bootstrap.write_text("# live bootstrap launcher\n")
        self._sync = updater._feed_sync
        updater._feed_sync = lambda adapter, deadline: True
        self.installs = []
        self._catalog = patch.object(updater, "_release_catalog", local_release_catalog)
        self._catalog.start()
        self.addCleanup(self._catalog.stop)

    def tearDown(self):
        updater._feed_sync = self._sync
        os.environ.pop("MINDIE_KIMI_CONFIG", None)
        self._tmp.cleanup()

    def fake_install(self, adapter, package, deadline, reserve=0.0):
        package = Path(package)
        manifest = json.loads((package / "kimi.plugin.json").read_text())
        mcp_names = list((manifest.get("mcpServers") or {}).keys())
        commands = list((package / "commands").rglob("*.md")) if (package / "commands").is_dir() else []
        record = dict(
            id=manifest["name"], version=manifest["version"],
            enabled=True, state="ok", hasErrors=False,
            originalSource=str(package.resolve()),
            skillCount=1 if (package / "skills").is_dir() else 0,
            mcpServerCount=len(mcp_names),
            enabledMcpServerCount=len(mcp_names),
            hookCount=len(manifest.get("hooks") or []),
            commandCount=len(commands),
        )
        install_data = dict(
            record,
            mcpServers=[{"name": name, "enabled": True, "transport": "stdio"}
                        for name in mcp_names],
            diagnostics=[],
        )
        self.installs.append((package, manifest))
        return {"install": {"body": {"data": install_data}},
                "after": {"body": {"data": {"plugins": [dict(record)]}}}}

    def run_check(self, **kw):
        args = dict(build=fake_build, install=self.fake_install)
        args.update(kw)
        return updater.check(self.adapter, **args)

    def test_failed_cached_domain_refresh_is_rebuilt_on_retry(self):
        deadline = time.monotonic() + updater.RESERVE + 30
        generation, _, _ = updater.stage_generation(
            self.sha, str(self.bare), self.adapter, deadline, build=fake_build)
        self.assertTrue((generation / updater.COMPLETE).is_file())
        active = json.loads(self.config_path.read_text())
        manual_engine = self.tmp / "manual.engine.json"
        engine = json.loads(Path(active["engine_config"]).read_text())
        write_json(manual_engine, {**engine, "domain": "manual"})
        active["domain_configs"] = {"manual": str(manual_engine)}
        write_json(self.config_path, active)
        # The fixture uses the already built test interpreter for both builds.
        with patch.object(updater, "_venv_python", return_value=Path(sys.executable)):
            with patch.object(updater, "build_host_package", side_effect=OSError("disk write failed")):
                with self.assertRaises(OSError):
                    updater.stage_generation(self.sha, str(self.bare), active, deadline, build=fake_build)
            self.assertFalse((generation / updater.COMPLETE).exists())
            rebuilt, _, config = updater.stage_generation(
                self.sha, str(self.bare), active, deadline, build=fake_build)
        self.assertEqual(rebuilt, generation)
        self.assertTrue((rebuilt / updater.COMPLETE).is_file())
        self.assertTrue((rebuilt / "host-package" / "kimi.plugin.json").is_file())
        self.assertEqual(json.loads(config.read_text())["domain_source_configs"], active["domain_configs"])

    def test_generation_refresh_prepares_only_the_two_main_public_feeds(self):
        active = json.loads(self.config_path.read_text())
        active["profile_config"] = str(self.config_path)
        engine_path = Path(active["engine_config"])
        engine = json.loads(engine_path.read_text())
        engine["feeds"] = [dict(repository="mindie-agent/knowledge-vllm-ascend",
                                ref="main", domain="vllm-ascend", interval_seconds=300)]
        write_json(engine_path, engine)
        community_path = Path(active["community_config"])
        consent_path = Path(json.loads(community_path.read_text())["consent_config"])
        consent_before = consent_path.read_bytes()
        write_json(self.config_path, active)
        generated = updater.write_generation_configs(
            self.tmp / "generation-main", Path(sys.executable), active)
        generated_adapter = json.loads(generated.read_text())
        generated_engine = json.loads(Path(generated_adapter["engine_config"]).read_text())
        self.assertEqual(generated_engine["feeds"][0]["repository"],
                         "mindie-agent/knowledge-vllm-ascend")
        self.assertEqual(generated_engine["feeds"][0]["ref"], "main")
        routes = generated_adapter["domain_configs"]
        self.assertEqual(set(routes), {"vllm-ascend", "operators"})
        self.assertEqual(routes["vllm-ascend"], generated_adapter["engine_config"])
        route = json.loads(Path(routes["operators"]).read_text())
        self.assertEqual(route["feeds"][0]["repository"],
                         "mindie-agent/knowledge-operators")
        self.assertEqual(route["feeds"][0]["ref"], "main")
        settings = json.loads(Path(route["community_config"]).read_text())
        self.assertEqual(settings["repository"], "mindie-agent/knowledge-operators")
        self.assertEqual(settings["branch"], "main")
        self.assertFalse(settings["enabled"])
        self.assertEqual(Path(settings["consent_config"]).resolve(), consent_path.resolve())
        self.assertEqual(consent_path.read_bytes(), consent_before)

    def test_generation_refresh_preserves_custom_feed_route_and_consent(self):
        active = json.loads(self.config_path.read_text())
        active["profile_config"] = str(self.config_path)
        engine_path = Path(active["engine_config"])
        engine = json.loads(engine_path.read_text())
        engine["feeds"] = [dict(repository="example/custom-vllm", ref="release-x",
                                domain="vllm-ascend", interval_seconds=120)]
        write_json(engine_path, engine)
        community_path = Path(active["community_config"])
        community = json.loads(community_path.read_text())
        community.update(repository="example/custom-vllm", branch="release-x")
        write_json(community_path, community)
        consent_path = Path(community["consent_config"])
        consent_before = consent_path.read_bytes()
        custom_route = self.tmp / "custom-operators.engine.json"
        write_json(custom_route, dict(
            engine, domain="operators", root=str(self.tmp / "custom-operators-data"),
            feeds=[dict(repository="example/custom-operators", ref="pinned",
                        domain="operators")]))
        custom_bytes = custom_route.read_bytes()
        active["domain_configs"] = {"operators": str(custom_route)}
        write_json(self.config_path, active)
        generated = updater.write_generation_configs(
            self.tmp / "generation-custom", Path(sys.executable), active)
        generated_adapter = json.loads(generated.read_text())
        generated_engine = json.loads(Path(generated_adapter["engine_config"]).read_text())
        self.assertEqual(generated_engine["feeds"], engine["feeds"])
        self.assertEqual(generated_adapter["domain_configs"]["operators"], str(custom_route))
        self.assertEqual(custom_route.read_bytes(), custom_bytes)
        self.assertEqual(json.loads(community_path.read_text())["repository"],
                         "example/custom-vllm")
        self.assertEqual(json.loads(community_path.read_text())["branch"], "release-x")
        self.assertEqual(consent_path.read_bytes(), consent_before)

    def test_unpublished_main_commit_never_changes_installed_release(self):
        self.assertEqual(self.run_check(), 0)
        before = genstate.current_path(self.adapter).read_bytes()
        (self.src / "marker.txt").write_text("unpublished")
        git(["commit", "-am", "unpublished"], self.src)
        git(["push", str(self.bare), "main"], self.src)
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(genstate.current_path(self.adapter).read_bytes(), before)
        self.assertEqual(len(self.installs), 1)

    def test_draft_and_prerelease_keep_the_current_generation(self):
        self.assertEqual(self.run_check(), 0)
        before = genstate.current_path(self.adapter).read_bytes()
        commit(self.src, self.bare, "unreleased")
        catalog_path = self.tmp / "releases.json"
        catalog = json.loads(catalog_path.read_text())
        for field in ("draft", "prerelease"):
            catalog[-1].update(draft=False, prerelease=False)
            catalog[-1][field] = True
            write_json(catalog_path, catalog)
            self.assertEqual(self.run_check(), 0)
            self.assertEqual(genstate.current_path(self.adapter).read_bytes(), before)
        self.assertEqual(len(self.installs), 1)

    def test_release_tag_mismatch_fails_before_build_or_install(self):
        (self.src / "marker.txt").write_text("moved tag")
        git(["commit", "-am", "moved tag"], self.src)
        tag = "v" + MANIFEST["version"]
        git(["tag", "-f", tag], self.src)
        git(["push", "--force", str(self.bare), tag], self.src)
        build_calls = []
        self.assertEqual(self.run_check(
            build=lambda *args: build_calls.append(args),
        ), 1)
        self.assertEqual(build_calls, [])
        self.assertEqual(self.installs, [])
        self.assertFalse(genstate.current_path(self.adapter).exists())
        self.assertIn("tag does not match", genstate.read_status(self.adapter)["error"])

    def test_source_version_must_match_release_before_runtime_build(self):
        catalog_path = self.tmp / "releases.json"
        catalog = json.loads(catalog_path.read_text())
        catalog[0]["metadata"]["version"] = "0.2.0"
        catalog[0]["tag_name"] = "v0.2.0"
        write_json(catalog_path, catalog)
        git(["tag", "v0.2.0"], self.src)
        git(["push", str(self.bare), "v0.2.0"], self.src)
        build_calls = []
        self.assertEqual(self.run_check(
            build=lambda *args: build_calls.append(args),
        ), 1)
        self.assertEqual(build_calls, [])
        self.assertEqual(self.installs, [])
        self.assertFalse(genstate.current_path(self.adapter).exists())
        self.assertIn("formal Release metadata", genstate.read_status(self.adapter)["error"])

    def test_unsupported_host_has_no_install_side_effect(self):
        catalog_path = self.tmp / "releases.json"
        catalog = json.loads(catalog_path.read_text())
        declaration = catalog[0]["metadata"]["compatibility"]
        intervals = [dict(minimum="2.3.0", maximum="2.3.0",
                          maximum_inclusive=True, include_prerelease=False)]
        declaration["supported"] = intervals
        for row in declaration["interface_registry"].values():
            row["supported"] = intervals
        write_json(catalog_path, catalog)
        self.assertEqual(self.run_check(), 1)
        self.assertEqual(genstate.read_status(self.adapter)["result"], "no_compatible_release")
        self.assertEqual(self.installs, [])
        self.assertFalse(genstate.current_path(self.adapter).exists())

    def test_git_argv_uses_the_stamped_binary(self):
        from unittest.mock import patch

        self.adapter["git_bin"] = "/pinned/git"
        write_json(self.config_path, self.adapter)
        calls = []
        with patch.object(updater, "bounded_run",
                          side_effect=lambda argv, stdin, **kw: calls.append(list(argv)) or ""):
            updater._git(["ls-remote", "x"], timeout=5,
                         deadline=time.monotonic() + 240)
        self.assertEqual(calls[0][0], "/pinned/git")
        with patch.dict(os.environ, {"MINDIE_KIMI_GIT_BIN": "/env/git"}):
            with patch.object(updater, "bounded_run",
                              side_effect=lambda argv, stdin, **kw: calls.append(list(argv)) or ""):
                updater._git(["fetch", "y"], timeout=5,
                             deadline=time.monotonic() + 240)
        self.assertEqual(calls[1][0], "/env/git")

    def test_kimi_bin_propagates_from_stamped_adapter_to_catalog_and_native_install(self):
        from unittest.mock import patch

        adapter = dict(self.adapter, kimi_bin="/pinned/kimi", kimi_home=str(self.tmp / "kimi-home"))
        calls = []

        def fake_run(argv, stdin, **kw):
            calls.append((list(argv), kw.get("env")))
            return "{}"

        self._catalog.stop()
        with patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}, clear=False), \
                patch.object(updater, "bounded_run", side_effect=fake_run):
            os.environ.pop("MINDIE_KIMI_BIN", None)
            updater._release_catalog(adapter, {}, time.monotonic() + 240)
            updater._readback(adapter, time.monotonic() + 240)
        self.assertIn("/pinned/kimi", calls[0][0])
        self.assertEqual(calls[1][1]["MINDIE_KIMI_BIN"], "/pinned/kimi")

    def test_scheduled_check_resolves_with_pinned_git_under_stripped_path(self):
        import shutil as shutil_mod

        git = shutil_mod.which("git")
        self.assertIsNotNone(git, "test requires git on PATH")
        self.adapter["git_bin"] = git
        write_json(self.config_path, self.adapter)
        from unittest.mock import patch

        # Session-0-style environment: no git and no uv on PATH. The pinned
        # binary resolves the remote anyway; the build keeps its own fakes.
        with patch.dict(os.environ, {"PATH": "/nonexistent-mindie"}):
            self.assertEqual(self.run_check(), 0)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "switched")
        self.assertNotIn("resolve_failure_class", status)

    def test_unstamped_check_resolves_via_standard_dir_and_switches(self):
        """The pre-fix-install scenario: no stamp, Session-0 PATH, and a
        standard Git-for-Windows layout — the first stamp can now happen."""
        import shutil as shutil_mod
        import types

        git = shutil_mod.which("git")
        self.assertIsNotNone(git, "test requires git on PATH")
        # Git for Windows resolves helpers relative to its installed executable.
        standard = Path(git).resolve()
        self.assertNotIn("git_bin", self.adapter)
        # Simulate the nt standard-dir branch with real paths: replace
        # paths.os (the module uses only environ/path/name) instead of
        # os.name, so pathlib never instantiates the wrong concrete class.
        fake_os = types.SimpleNamespace(
            name="nt", environ=os.environ, path=os.path
        )
        os.environ.pop("MINDIE_KIMI_GIT_BIN", None)
        from unittest.mock import patch

        import paths

        with patch("paths.shutil.which", return_value=None), \
             patch("paths.GIT_WINDOWS_LOCATIONS", (str(standard),)), \
             patch.object(paths, "os", fake_os):
            self.assertEqual(updater._git_bin(), str(standard))
            self.assertEqual(self.run_check(), 0)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "switched")

    def test_git_bin_falls_back_to_bare_git_when_nothing_resolves(self):
        from unittest.mock import patch

        self.assertNotIn("git_bin", self.adapter)
        os.environ.pop("MINDIE_KIMI_GIT_BIN", None)
        with patch("paths.shutil.which", return_value=None), \
             patch("paths.GIT_WINDOWS_LOCATIONS", (str(self.tmp / "no-std-git"),)):
            self.assertEqual(updater._git_bin(), "git")

    def test_missing_git_is_an_honest_failure_not_a_network_one(self):
        from unittest.mock import patch

        self.assertNotIn("git_bin", self.adapter)
        # A stamp pointing at a moved/deleted binary (or no git anywhere):
        # the spawn itself must fail, deterministically on every host —
        # scrubbing PATH alone does not stop Windows from resolving a bare
        # "git" through App Paths or a Git-for-Windows standard install.
        missing = str(self.tmp / "mindie-no-such-git")
        with patch.object(updater, "_git_bin", return_value=missing):
            self.assertEqual(self.run_check(), 1)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "check-failed")
        self.assertIsNone(status["resolve_failure_class"])
        self.assertNotEqual(
            status.get("error"), updater._STATIC_FAILURE["temporary_network"]
        )
        # The missing-file evidence is platform-shaped: POSIX strerror
        # includes the path; Windows FileNotFoundError reports only
        # WinError 2 (the path lives on the exception's filename attribute
        # and never reaches this string).
        self.assertTrue(
            "mindie-no-such-git" in status["error"]
            or "WinError 2" in status["error"]
            or "No such file or directory" in status["error"],
            status["error"],
        )

    def test_feed_sync_carries_stamped_git_bin_to_the_component(self):
        from unittest.mock import patch

        calls = []
        run = lambda argv, stdin, **kw: calls.append((list(argv), kw)) or "[]"
        genstate.update_dir(self.adapter).mkdir(parents=True, exist_ok=True)
        with patch.object(updater, "bounded_run", side_effect=run):
            self.assertTrue(self._sync(
                dict(self.adapter, git_bin="/pinned/git"),
                time.monotonic() + 240,
            ))
            self.assertTrue(self._sync(self.adapter, time.monotonic() + 240))
        stamped = calls[0][1]["env"]["MINDIE_GIT_BIN"]
        self.assertEqual(stamped, "/pinned/git")
        self.assertIsNone(calls[1][1]["env"])

    def test_restore_env_carries_git_bin_when_stamped(self):
        from unittest.mock import patch

        generation = self.tmp / "restored-gen"
        generation.mkdir()
        final = dict(
            generation=str(generation),
            python=str(Path(sys.executable)),
            adapter_config=str(self.tmp / "restored-adapter.json"),
            sha=self.sha,
        )
        write_json(
            self.tmp / "restored-adapter.json",
            dict(
                engine_config=str(self.tmp / "restored-engine.json"),
                git_bin="/pinned/git",
            ),
        )
        genstate.write_current(final, self.adapter)
        envs = []
        run = lambda argv, stdin, **kw: envs.append(kw.get("env")) or '{"status": "restored"}'
        with patch.object(updater, "bounded_run", side_effect=run):
            updater._restore_stopped_service(
                self.adapter, final, time.monotonic() + 240
            )
        self.assertEqual(len(envs), 1)
        self.assertEqual(envs[0]["MINDIE_GIT_BIN"], "/pinned/git")
        self.assertNotIn("MINDIE_KIMI_BIN", envs[0])

    def test_switch_commits_one_tuple_and_preserves_stable_state(self):
        # Default idle: REAL stop_if_idle subprocess through the committed
        # interpreter; no service endpoint exists here, so it is idle.
        result = self.run_check()
        self.assertEqual(result, 0)
        current = genstate.read_current(self.adapter)
        self.assertEqual(current["sha"], self.sha)
        generation = Path(current["generation"])
        self.assertTrue((generation / updater.COMPLETE).is_file())
        self.assertEqual(current["python"],
                         str(Path(sys.executable)))
        gen_adapter = Path(current["adapter_config"])
        self.assertEqual(gen_adapter, generation / "config" / "kimi.adapter.json")
        # Generation adapter keeps stable state paths, new interpreter.
        gen_value = json.loads(gen_adapter.read_text())
        self.assertEqual(gen_value["state_dir"], self.adapter["state_dir"])
        self.assertEqual(gen_value["community_config"],
                         self.adapter["community_config"])
        self.assertEqual(gen_value["python"], current["python"])
        gen_engine = json.loads(Path(gen_value["engine_config"]).read_text())
        base_engine = json.loads(
            (self.tmp / "cfg" / "kimi.engine.json").read_text())
        self.assertEqual(gen_engine["admission_path"],
                         base_engine["admission_path"])
        self.assertEqual(gen_engine["root"], base_engine["root"])
        self.assertTrue(gen_engine["transcript_adapter"].startswith(
            str(generation)))
        # Manifest points at the NEW versioned launcher; live one untouched.
        manifest = self.installs[0][1]
        self.assertTrue(manifest["version"].endswith(f"+mindie.{self.sha[:12]}"))
        args = manifest["mcpServers"]["knowledge"]["args"]
        launcher_dir = genstate.launch_dir(self.adapter) / self.sha
        self.assertEqual(Path(args[0]), launcher_dir / "mindie_launch.py")
        self.assertEqual(
            args[1:],
            ["--config", str(generation / "config" / "kimi.adapter.json"),
             "mcp", "knowledge"],
        )
        command = manifest["mcpServers"]["knowledge"]["command"]
        self.assertTrue(updater.host_valid_mcp_command(command), command)
        if os.name == "nt":
            self.assertEqual(command, Path(sys.executable).name)
            wrapper = Path(sys.executable)
        else:
            self.assertTrue(command.startswith("./"))
            wrapper = self.installs[0][0] / command[2:]
            self.assertTrue(wrapper.is_file())
        launched = subprocess.run(
            [str(wrapper), "-c", "import sys; print(sys.executable)"],
            capture_output=True, text=True, timeout=5, check=True,
        )
        self.assertEqual(Path(launched.stdout.strip()).resolve(),
                         Path(sys.executable).resolve())
        # The front's bounded.py dependency is copied next to the launcher.
        self.assertTrue((launcher_dir / "bounded.py").is_file())
        bootstrap = genstate.launch_dir(self.adapter) / "bootstrap" / "mindie_launch.py"
        self.assertEqual(bootstrap.read_text(), "# live bootstrap launcher\n")
        receipt = genstate.read_json(
            genstate.receipts_dir(self.adapter) / f"{self.sha}.json")
        self.assertEqual(receipt["previous"]["sha"], None)
        self.assertTrue(genstate.read_status(self.adapter)["needs_host_reload"])

    def test_current_when_main_matches(self):
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(len(self.installs), 1)
        self.assertEqual(genstate.read_status(self.adapter)["result"], "current")

    def test_busy_defers_and_keeps_current(self):
        def busy(adapter, current, deadline):
            raise updater.Deferred("service has actual active work; switch deferred")

        self.assertEqual(self.run_check(idle=busy), 0)
        self.assertEqual(self.installs, [])
        self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        self.assertEqual(genstate.read_status(self.adapter)["result"], "deferred")

    def test_idle_uses_reviewed_updater_helper_not_old_generation_scripts(self):
        empty = self.tmp / "empty-gen"
        (empty / "scripts").mkdir(parents=True)
        write_json(empty / "kimi.plugin.json", dict(MANIFEST, version="0.0.1"))
        genstate.write_current({
            "generation": str(empty),
            "python": sys.executable,
            "adapter_config": str(self.config_path),
            "sha": None,
        }, self.adapter)
        result = self.run_check()
        self.assertEqual(result, 0)
        self.assertEqual(len(self.installs), 1)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "switched")

if __name__ == "__main__":
    unittest.main()
