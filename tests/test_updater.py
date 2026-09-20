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


def make_remote(tmp: Path, marker="one") -> tuple[Path, str]:
    src = tmp / "remote-src"
    bare = tmp / "remote.git"
    (src / "scripts").mkdir(parents=True)
    (src / "skills").mkdir()
    (src / "commands").mkdir()
    write_json(src / "kimi.plugin.json", MANIFEST)
    (src / "runtime-requirements.txt").write_text(
        "mindie-knowledge @ git+https://github.com/mindie-agent/knowledge"
        "@6155846c99b454e3d1c436a6dcfbae82d0ba7e51\n"
    )
    (src / "scripts" / "mindie_launch.py").write_text("# stable launcher\n")
    (src / "scripts" / "transcript.py").write_text("# parser\n")
    (src / "scripts" / "organizer.py").write_text("# organizer\n")
    (src / "marker.txt").write_text(marker)
    git(["init", "-b", "main"], src)
    git(["add", "-A"], src)
    git(["commit", "-m", marker], src)
    sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=src, check=True,
                         capture_output=True, text=True, env=GIT_ENV).stdout.strip()
    git(["init", "--bare", str(bare)], tmp)
    git(["push", str(bare), "main"], src)
    return bare, sha


def fake_build(generation: Path) -> Path:
    # Test seam for updater's `build` parameter (no network in tests):
    # a wrapper that execs the installed acceptance runtime, which already
    # provides the pinned mindie_knowledge/remote_dev. Production uses the
    # real updater.build_runtime (fresh venv + pinned pip install).
    python = generation / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return python


def no_sync(adapter, status):
    return None


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.tmp = tmp
        self.bare, self.sha = make_remote(tmp)
        self.config_path = make_config(tmp / "cfg", sharing=True,
                                       roots=[tmp / "cfg"])
        data = json.loads(self.config_path.read_text())
        data["update_remote"] = str(self.bare)
        data["kimi_home"] = str(tmp / "kimi-home")
        write_json(self.config_path, data)
        self.adapter = data
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config_path)
        self._sync = updater._feed_sync
        updater._feed_sync = no_sync  # scheduler sync is core-side; not under test
        self.installs = []

    def tearDown(self):
        updater._feed_sync = self._sync
        os.environ.pop("MINDIE_KIMI_CONFIG", None)
        self._tmp.cleanup()

    def fake_install(self, adapter, package):
        manifest = json.loads((package / "kimi.plugin.json").read_text())
        self.installs.append(manifest)
        return {"install": {"body": {"data": {"id": "mindie-agent"}}},
                "after": {"body": {"plugins": [{"id": "mindie-agent",
                                                "version": "0.1.0"}]}}}

    def idle(self, adapter):
        return {"idle": True}

    def run_check(self, **kw):
        args = dict(build=fake_build, idle=self.idle, install=self.fake_install)
        args.update(kw)
        return updater.check(self.adapter, **args)

    def test_switch_applies_and_idle_grants_survive(self):
        import admission as admission_mod

        lease = admission_mod.activate("ses_upd",
                                       project_root=str(self.tmp / "cfg"))
        token = lease["token"]
        remote_state = genstate.state_dir(self.adapter) / "remote" / "ses_upd"
        remote_state.mkdir(parents=True)
        (remote_state / "task.json").write_text("{}\n")
        result = self.run_check()
        self.assertEqual(result, 0)
        current = genstate.read_current(self.adapter)
        self.assertEqual(current["sha"], self.sha)
        generation = Path(current["generation"])
        self.assertTrue((generation / updater.COMPLETE).is_file())
        self.assertEqual(current["python"],
                         str(generation / ".venv" / "bin" / "python"))
        # Host package points at the stable absolute launcher, not ./scripts.
        manifest = self.installs[0]
        args = manifest["mcpServers"]["knowledge"]["args"]
        self.assertTrue(os.path.isabs(args[0]))
        self.assertEqual(args[1:], ["mcp", "knowledge"])
        self.assertIn("hook stop", manifest["hooks"][1]["command"])
        # Same admission/store paths; new parser/organizer generation paths.
        engine = json.loads(
            (self.tmp / "cfg" / "kimi.engine.json").read_text())
        self.assertEqual(engine["admission_path"],
                         str(self.tmp / "cfg" / "domain" / "admission.sqlite3"))
        self.assertTrue(engine["transcript_adapter"].startswith(str(generation)))
        self.assertTrue(engine["agent_command"][1].startswith(str(generation)))
        # Transaction receipt for rollback exists.
        receipt = genstate.read_json(
            genstate.receipts_dir(self.adapter) / f"{self.sha}.json")
        self.assertEqual(receipt["sha"], self.sha)
        # Idle grant survives the version switch; remote task-state path stable.
        gate = admission_mod.gate()
        self.assertEqual(gate.resolve(token)["session"], "ses_upd")
        self.assertTrue((remote_state / "task.json").is_file())
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "switched")
        self.assertTrue(status["needs_host_reload"])

    def test_current_when_main_matches(self):
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(len(self.installs), 1)
        self.assertEqual(genstate.read_status(self.adapter)["result"], "current")

    def test_busy_defers_and_keeps_current(self):
        result = self.run_check(idle=lambda adapter: {"idle": False})
        self.assertEqual(result, 0)
        self.assertEqual(self.installs, [])
        self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        self.assertEqual(genstate.read_status(self.adapter)["result"],
                         "deferred")

    def test_missing_stop_if_idle_defers_honestly(self):
        def unavailable(adapter):
            raise updater.Deferred("pinned core does not provide stop_if_idle")

        result = self.run_check(idle=unavailable)
        self.assertEqual(result, 0)
        self.assertEqual(self.installs, [])
        self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "deferred")
        self.assertIn("stop_if_idle", status["error"])

    def test_shared_lock_blocks_exclusive_switch(self):
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, " + repr(str(SCRIPTS)) + ");"
             "import genstate;"
             "lock = genstate.OperationLock();"
             "held = lock.shared(timeout=5); held.__enter__();"
             "print('held', flush=True); time.sleep(4)"],
            stdout=subprocess.PIPE, text=True,
            env=dict(os.environ, MINDIE_KIMI_CONFIG=str(self.config_path)),
        )
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            result = self.run_check(lock_timeout=0.5)
            self.assertEqual(result, 0)
            self.assertEqual(self.installs, [])
            self.assertIsNone(genstate.read_current(self.adapter)["sha"])
            self.assertEqual(genstate.read_status(self.adapter)["result"],
                             "deferred-busy")
        finally:
            holder.terminate()
            holder.wait(timeout=5)

    def test_interrupted_staging_suppresses_and_recovers(self):
        def broken(generation):
            raise RuntimeError("venv creation interrupted")

        result = self.run_check(build=broken)
        self.assertEqual(result, 1)
        self.assertEqual(self.installs, [])
        failed = genstate.read_json(genstate.failed_path(self.adapter))
        self.assertEqual(failed["sha"], self.sha)
        leftovers = [p for p in genstate.generations_dir(self.adapter).iterdir()
                     if p.name.startswith(".staging")]
        self.assertEqual(leftovers, [])
        self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        # Same failed revision is suppressed; installer is never called.
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(self.installs, [])
        self.assertEqual(genstate.read_status(self.adapter)["result"],
                         "suppressed-known-failed")
        # A newer revision is not suppressed.
        src = self.tmp / "remote-src"
        (src / "marker.txt").write_text("two")
        git(["add", "-A"], src)
        git(["commit", "-m", "two"], src)
        git(["push", str(self.bare), "main"], src)
        self.assertEqual(self.run_check(), 0)
        self.assertEqual(len(self.installs), 1)
        # Explicit recovery re-checks a previously failed exact revision.
        (src / "marker.txt").write_text("three")
        git(["add", "-A"], src)
        git(["commit", "-m", "three"], src)
        git(["push", str(self.bare), "main"], src)
        self.assertEqual(self.run_check(build=broken), 1)
        self.assertEqual(self.run_check(force=True), 0)
        self.assertEqual(len(self.installs), 2)

    def test_install_failure_rolls_back_configs(self):
        def bad_install(adapter, package):
            raise updater.CheckFailed("native install rejected")

        result = self.run_check(install=bad_install)
        self.assertEqual(result, 1)
        self.assertIsNone(genstate.read_current(self.adapter)["sha"])
        engine = json.loads((self.tmp / "cfg" / "kimi.engine.json").read_text())
        self.assertTrue(engine["transcript_adapter"].endswith(
            str(Path("scripts") / "transcript.py")))
        adapter = json.loads(self.config_path.read_text())
        self.assertEqual(adapter["python"], sys.executable)


if __name__ == "__main__":
    unittest.main()
