import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import SCRIPTS, make_config, write_json

sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import updater  # noqa: E402

GIT_ENV = dict(
    os.environ,
    GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
    GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
)

MANIFEST = {
    "name": "mindie-agent",
    "version": "0.1.0",
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
    (src / "marker.txt").write_text(marker)
    git(["add", "-A"], src)
    git(["commit", "-m", marker], src)
    git(["push", str(bare), "main"], src)


def make_remote(tmp: Path):
    src = tmp / "remote-src"
    bare = tmp / "remote.git"
    (src / "scripts").mkdir(parents=True)
    (src / "skills").mkdir()
    (src / "commands").mkdir()
    write_json(src / "kimi.plugin.json", MANIFEST)
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

    for name in ("knowledge_service.py", "paths.py", "transcript.py"):
        shutil.copy2(SCRIPTS / name, src / "scripts" / name)
    (src / "marker.txt").write_text("one")
    git(["init", "-b", "main"], src)
    git(["add", "-A"], src)
    git(["commit", "-m", "one"], src)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True,
                         capture_output=True, text=True, env=GIT_ENV).stdout.strip()
    git(["init", "--bare", str(bare)], tmp)
    git(["push", str(bare), "main"], src)
    return src, bare, sha


def fake_build(generation: Path, deadline: float) -> Path:
    # Test seam for updater's `build` parameter (no network in tests):
    # a wrapper that execs the installed acceptance runtime, which already
    # provides the pinned mindie_knowledge/remote_dev. Production uses the
    # real updater.build_runtime (fresh venv + pinned pip install).
    python = generation / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return python


class UpdaterTests(unittest.TestCase):
    def setUp(self):
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
                         str(generation / ".venv" / "bin" / "python"))
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

    def test_idle_fails_closed_without_runtime(self):
        empty = self.tmp / "empty-gen"
        (empty / "scripts").mkdir(parents=True)
        genstate.write_current({
            "generation": str(empty),
            "python": sys.executable,
            "adapter_config": str(self.config_path),
            "sha": None,
        }, self.adapter)
        result = self.run_check()
        self.assertEqual(result, 0)
        self.assertEqual(self.installs, [])
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "deferred")
        self.assertIn("runtime import failed", status["error"])

    def test_shared_lock_blocks_exclusive_switch(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, " + repr(str(SCRIPTS)) + ");"
             "import genstate;"
             "held = genstate.OperationLock().shared(timeout=5);"
             "held.__enter__(); print('held', flush=True); time.sleep(4)"],
            stdout=subprocess.PIPE, text=True,
            env=dict(os.environ, MINDIE_KIMI_CONFIG=str(self.config_path)),
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.assertEqual(self.run_check(lock_timeout=0.5), 0)
            self.assertEqual(self.installs, [])
            self.assertIsNone(genstate.read_current(self.adapter)["sha"])
            self.assertEqual(genstate.read_status(self.adapter)["result"],
                             "deferred-busy")
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_check_lock_serializes_checks(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, " + repr(str(SCRIPTS)) + ");"
             "import genstate;"
             "held = genstate.check_lock();"
             "held.__enter__(); print('held', flush=True); time.sleep(4)"],
            stdout=subprocess.PIPE, text=True,
            env=dict(os.environ, MINDIE_KIMI_CONFIG=str(self.config_path)),
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            self.assertEqual(self.run_check(), 0)
            self.assertEqual(self.installs, [])
            self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_interrupted_staging_suppresses_and_recovers(self):
        calls = []
        updater._feed_sync = lambda adapter, deadline: calls.append("sync") or True

        def broken(generation, deadline):
            raise RuntimeError("venv creation interrupted")

        self.assertEqual(self.run_check(build=broken), 1)
        self.assertEqual(self.installs, [])
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertEqual(failed["sha"], self.sha)
        leftovers = [p for p in genstate.generations_dir(self.adapter).iterdir()
                     if p.name.startswith(".staging") or
                     (p.is_dir() and not (p / updater.COMPLETE).exists())]
        self.assertEqual(leftovers, [])
        self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        # Feed sync is independent: it ran even though the candidate failed.
        self.assertEqual(calls, ["sync"])
        # Same failed revision suppressed; installer never called.
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(self.installs, [])
        self.assertEqual(genstate.read_status(self.adapter)["result"],
                         "suppressed-known-failed")
        # Newer revision is not suppressed; explicit recovery re-checks.
        commit(self.src, self.bare, "two")
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(len(self.installs), 1)
        commit(self.src, self.bare, "three")
        self.assertEqual(self.run_check(build=broken), 1)
        self.assertEqual(self.run_check(force=True), 0)
        self.assertEqual(len(self.installs), 2)

    def test_failed_switch_restores_pointer_and_native_package(self):
        self.assertEqual(self.run_check(), 0)
        first = genstate.read_current(self.adapter)
        package_a = self.installs[0][0]
        commit(self.src, self.bare, "two")
        real_write = updater.write_current
        attempts = []

        def flaky(value, config=None):
            attempts.append(value)
            if len(attempts) == 1:
                raise OSError("disk hiccup during pointer flip")
            return real_write(value, config)

        updater.write_current = flaky
        try:
            self.assertEqual(self.run_check(), 1)
        finally:
            updater.write_current = real_write
        current = genstate.read_current(self.adapter)
        self.assertEqual(current["sha"], first["sha"])
        self.assertEqual(current["adapter_config"], first["adapter_config"])
        # Rollback reinstalled the previous native host package (readback
        # path), and the failure is recorded, never claimed as success.
        self.assertEqual(self.installs[-1][0], package_a)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "failed")
        self.assertIn("restored", status["rollback"])

    def test_feed_sync_uses_current_interpreter_and_config(self):
        updater._feed_sync = self._sync
        self.assertEqual(self.run_check(), 0)
        recorded = []
        real_run = updater.bounded_run

        def spy(argv, stdin="", **kw):
            recorded.append(argv)
            return "[]"

        updater.bounded_run = spy
        try:
            updater._feed_sync(self.adapter, time.monotonic() + 60)
        finally:
            updater.bounded_run = real_run
        current = genstate.read_current(self.adapter)
        argv = recorded[0]
        self.assertEqual(argv[0], current["python"])
        self.assertEqual(argv[1:4], ["-m", "mindie_knowledge.loop.cli", "sync"])
        self.assertEqual(argv[4], "--config")
        gen_adapter = json.loads(Path(current["adapter_config"]).read_text())
        self.assertEqual(argv[5], gen_adapter["engine_config"])
        self.assertEqual(genstate.read_status(self.adapter)["feed_sync"], "ok")


    def test_uncertain_native_outcome_rolls_back_under_same_lock(self):
        self.assertEqual(self.run_check(), 0)
        first = genstate.read_current(self.adapter)
        package_a = self.installs[0][0]
        commit(self.src, self.bare, "two")
        calls = []

        def uncertain(adapter, package, deadline, reserve=0.0):
            calls.append((Path(package), reserve))
            if len(calls) == 1:
                # Install attempt mutated native state, then raised without
                # a verdict: rollback must still restore the previous package.
                raise RuntimeError("connection lost after POST")
            # Rollback reinstall: the exclusive lock must still be held.
            with self.assertRaises(genstate.LockTimeout):
                with genstate.OperationLock(self.adapter).exclusive(timeout=0.1):
                    pass
            return self.fake_install(adapter, package, deadline, reserve)

        self.assertEqual(self.run_check(install=uncertain), 1)
        self.assertEqual(calls[1][0], package_a)
        # Rollback's reserved window ends where the feed reserve begins.
        self.assertEqual(calls[1][1], updater.FEED_BUDGET)
        current = genstate.read_current(self.adapter)
        self.assertEqual(current["sha"], first["sha"])
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "failed")
        self.assertIn("restored", status["rollback"])

    def test_failed_restore_is_reported_honestly(self):
        self.assertEqual(self.run_check(), 0)
        commit(self.src, self.bare, "two")

        def broken(adapter, package, deadline, reserve=0.0):
            raise RuntimeError("native API unreachable")

        self.assertEqual(self.run_check(install=broken), 1)
        status = genstate.read_status(self.adapter)
        self.assertIn("NOT proven restored", status["rollback"])

    def test_pointer_restore_failure_is_reported(self):
        self.assertEqual(self.run_check(), 0)
        first = genstate.read_current(self.adapter)
        commit(self.src, self.bare, "two")
        real_write = updater.write_current
        calls = []

        def flaky(value, config=None):
            calls.append(value.get("sha"))
            if len(calls) <= 2:
                raise OSError("write failed")
            return real_write(value, config)

        updater.write_current = flaky
        try:
            self.assertEqual(self.run_check(), 1)
        finally:
            updater.write_current = real_write
        self.assertEqual(genstate.read_current(self.adapter)["sha"],
                         first["sha"])
        status = genstate.read_status(self.adapter)
        self.assertTrue(status["pointer_restore"].startswith(
            "pointer restore FAILED"))

    def test_feed_sync_skips_truthfully_when_budget_exhausted(self):
        updater._feed_sync = self._sync
        calls = []
        real_run = updater.bounded_run
        updater.bounded_run = lambda *a, **k: calls.append(a) or ""
        try:
            updater._feed_sync(self.adapter, time.monotonic() - 1)
        finally:
            updater.bounded_run = real_run
        self.assertEqual(calls, [])
        self.assertEqual(genstate.read_status(self.adapter)["feed_sync"],
                         "skipped: check budget exhausted")

    def test_exact_readback_rejects_wrong_record(self):
        package = self.tmp / "pkg"
        package.mkdir()
        write_json(package / "kimi.plugin.json",
                   dict(MANIFEST, version="0.1.0+mindie.abc"))
        mcp_servers = [
            {"name": "knowledge", "enabled": True, "transport": "stdio"},
            {"name": "remote", "enabled": True, "transport": "stdio"},
        ]
        base = dict(id="mindie-agent", version="0.1.0+mindie.abc",
                    enabled=True, state="ok", hasErrors=False,
                    originalSource=str(package.resolve()),
                    skillCount=0, mcpServerCount=2, enabledMcpServerCount=2,
                    hookCount=2, commandCount=0,
                    mcpServers=mcp_servers, diagnostics=[])

        def report(record, install=None):
            payload = dict(base if install is None else install)
            return {"install": {"body": {"data": payload}},
                    "after": {"body": {"data": {"plugins": [record]}}}}

        manifest = json.loads((package / "kimi.plugin.json").read_text())
        updater._verify_native_record(report(dict(base)), manifest,
                                      package.resolve())
        for mutation in (
            dict(base, version="0.1.0"),
            dict(base, enabled=False),
            dict(base, state="error"),
            dict(base, originalSource="/elsewhere"),
            dict(base, mcpServerCount=0, enabledMcpServerCount=0),
        ):
            with self.assertRaises(updater.CheckFailed):
                updater._verify_native_record(report(mutation), manifest,
                                              package.resolve())
        with self.assertRaises(updater.CheckFailed):
            updater._verify_native_record(
                {"install": {"body": {"data": dict(base)}},
                 "after": {"body": {"data": {"plugins": []}}}},
                manifest, package.resolve())
        with self.assertRaises(updater.CheckFailed):
            updater._verify_native_record(
                {"install": {"body": {"data": dict(base)}},
                 "after": {"body": {"data": {}}}},
                manifest, package.resolve())
        skipped = dict(
            base, mcpServerCount=0, enabledMcpServerCount=0, mcpServers=[],
            diagnostics=[{
                "severity": "warn",
                "message": '"mcpServers.knowledge.command" must be a PATH command or start with "./"',
            }],
        )
        with self.assertRaises(updater.CheckFailed):
            updater._verify_native_record(
                report(dict(skipped), install=skipped), manifest,
                package.resolve())

    def test_host_package_mcp_command_is_native_valid(self):
        pkg = updater.build_host_package(
            self.src, self.adapter, self.sha, config_file=self.config_path)
        manifest = json.loads((pkg / "kimi.plugin.json").read_text())
        launcher = genstate.launch_dir(self.adapter) / self.sha / "mindie_launch.py"
        for name, server in manifest["mcpServers"].items():
            command = server["command"]
            self.assertTrue(updater.host_valid_mcp_command(command), command)
            self.assertTrue(command.startswith("./"), command)
            self.assertFalse(os.path.isabs(command))
            self.assertEqual(Path(server["args"][0]), launcher)
            self.assertEqual(
                server["args"][1:],
                ["--config", str(self.config_path), "mcp", name],
            )
            wrapper = pkg / command[2:]
            self.assertTrue(wrapper.is_file())
        self.assertFalse(updater.host_valid_mcp_command(sys.executable))
        self.assertFalse(updater.host_valid_mcp_command("/usr/bin/python3"))
        self.assertTrue(updater.host_valid_mcp_command("python3"))
        self.assertTrue(updater.host_valid_mcp_command("./mindie-front"))
        stop = next(h for h in manifest["hooks"] if h.get("event") == "Stop")
        self.assertIn("--config", stop["command"])
        self.assertIn(str(self.config_path), stop["command"])

    def test_schedule_command_encodes_config_not_env(self):
        launcher = Path("/tmp/mindie_launch.py")
        config = Path("/tmp/custom/kimi.json")
        command = updater.schedule_command(launcher, config)
        self.assertEqual(command[1:4], [str(launcher), "--config", str(config)])
        self.assertEqual(command[4:], ["updater", "check"])


if __name__ == "__main__":
    unittest.main()
