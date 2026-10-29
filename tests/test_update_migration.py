"""Auto-update packaging and legacy-generation migration contracts.

Uses the real 0.1.2/0.1.3 manifests as fixtures and the real candidate
manifest, launcher, MCP server and builder. No GitHub distribution is implied:
the sandbox uses only a private adapter state tree plus the pinned runtime.
"""
import io
import json
import os
import shlex
import shutil
import tarfile
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from support import ROOT, SCRIPTS, env_for, make_config, write_json

sys.path.insert(0, str(SCRIPTS))

import genstate  # noqa: E402
import mcp_server  # noqa: E402
import updater  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "manifests"
CANDIDATE = json.loads((ROOT / "kimi.plugin.json").read_text(encoding="utf-8"))
SHA = "c" * 40
LEGACY_TIMEOUT_MS = 660000
NATIVE_NAMES = {
    "remote_read", "remote_Read", "remote_Write", "remote_Edit",
    "remote_Bash", "remote_Glob", "remote_Grep", "remote_ReadMediaFile",
    "remote_TaskOutput", "remote_TaskStop", "remote_TaskList",
    "remote_auth",
}


def runtime_tool_names():
    from remote_dev.mcp.tools import list_tools
    return {item["name"] for item in list_tools("kimi")}


def make_generation(tmp: Path, manifest: dict) -> Path:
    generation = tmp / "generation"
    (generation / "scripts").mkdir(parents=True)
    for name in ("mindie_launch.py", "bounded.py", "diagnostic_support.py", "paths.py", "launch_budget.py"):
        shutil.copy2(SCRIPTS / name, generation / "scripts" / name)
    write_json(generation / "kimi.plugin.json", manifest)
    return generation


def package_manifest(package: Path) -> dict:
    return json.loads((package / "kimi.plugin.json").read_text(encoding="utf-8"))


class HostPackageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.config = make_config(self.tmp / "cfg")
        self.adapter = json.loads(self.config.read_text())

    def test_candidate_manifest_keeps_timeout_profile_and_host_fields(self):
        generation = make_generation(self.tmp, CANDIDATE)
        package = updater.build_host_package(
            generation, self.adapter, SHA, config_file=self.config,
        )
        remote = package_manifest(package)["mcpServers"]["remote"]
        candidate_remote = CANDIDATE["mcpServers"]["remote"]
        # This is Kimi's actual per-server host field, not an invented schema.
        self.assertEqual(remote["toolTimeoutMs"], candidate_remote["toolTimeoutMs"])
        self.assertEqual(remote["toolTimeoutMs"], LEGACY_TIMEOUT_MS)
        self.assertEqual(remote["cwd"], "./")
        self.assertEqual(
            remote["env"]["REMOTE_DEV_HARNESS_PROFILE"], "kimi",
        )
        self.assertEqual(Path(remote["args"][0]).name, "mindie_launch.py")
        self.assertEqual(remote["args"][-2:], ["mcp", "remote"])

    def test_legacy_012_and_013_manifests_gain_timeout_and_profile(self):
        for version in ("0.1.2", "0.1.3"):
            with self.subTest(version=version):
                source = json.loads(
                    (FIXTURES / f"kimi.plugin.{version}.json").read_text(encoding="utf-8")
                )
                generation = make_generation(self.tmp / version, source)
                package = updater.build_host_package(
                    generation, self.adapter, SHA, config_file=self.config,
                )
                remote = package_manifest(package)["mcpServers"]["remote"]
                self.assertEqual(remote["toolTimeoutMs"], LEGACY_TIMEOUT_MS)
                self.assertEqual(
                    remote["env"]["REMOTE_DEV_HARNESS_PROFILE"], "kimi",
                )

    def test_tools_list_without_outer_profile_uses_kimi_catalog(self):
        env = env_for(self.config)
        env.pop("REMOTE_DEV_HARNESS_PROFILE", None)
        if pinned := os.environ.get("MINDIE_TEST_REMOTE_DEV"):
            env["PYTHONPATH"] = pinned
        messages = (
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2025-11-25",
                                   "capabilities": {}, "clientInfo": {"name": "test"}}})
            + "\n"
            + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            + "\n"
        )
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "mcp_server.py"), "remote"],
            input=messages, text=True, encoding="utf-8",
            capture_output=True, timeout=30, env=env, cwd=str(ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        listing = next(row for row in replies if row.get("id") == 2)
        names = {tool["name"] for tool in listing["result"]["tools"]}
        self.assertEqual(names, runtime_tool_names())
        self.assertTrue(NATIVE_NAMES <= names, names)
        for tool in listing["result"]["tools"]:
            self.assertIn("request_nonce", tool["inputSchema"]["required"])


class ProfileLifecycleTests(unittest.TestCase):
    def test_remote_tools_binds_profile_before_discovery(self):
        saved = os.environ.pop("REMOTE_DEV_HARNESS_PROFILE", None)
        try:
            tools = mcp_server.remote_tools()
        finally:
            if saved is not None:
                os.environ["REMOTE_DEV_HARNESS_PROFILE"] = saved
            else:
                os.environ.pop("REMOTE_DEV_HARNESS_PROFILE", None)
        names = {tool["name"] for tool in tools}
        self.assertEqual(names, runtime_tool_names())
        self.assertTrue(NATIVE_NAMES <= names, names)
        self.assertNotIn("remote_exec_command", names)

    def test_launcher_child_env_binds_remote_profile(self):
        import mindie_launch

        current = {"adapter_config": str(Path("/tmp/adapter.json"))}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REMOTE_DEV_HARNESS_PROFILE", None)
            remote = mindie_launch._child_env(current, "remote")
            knowledge = mindie_launch._child_env(current, "knowledge")
        self.assertEqual(remote["REMOTE_DEV_HARNESS_PROFILE"], "kimi")
        self.assertNotIn("REMOTE_DEV_HARNESS_PROFILE", knowledge)


class LegacyRepairTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.config = make_config(self.tmp / "cfg")
        self.adapter = json.loads(self.config.read_text())
        self.generation = make_generation(self.tmp, CANDIDATE)
        self.current = dict(
            generation=str(self.generation), python=sys.executable,
            adapter_config=str(self.config), sha=SHA,
        )
        genstate.write_current(self.current, self.adapter)

    def _legacy_receipt(self):
        path = genstate.receipts_dir(self.adapter) / f"{SHA}.json"
        write_json(path, {"sha": SHA, "at": time.time(), "previous": None,
                          "generation": str(self.generation)})
        return path

    def test_legacy_receipt_is_repaired_once_with_candidate_fields(self):
        receipt_path = self._legacy_receipt()
        installed = []

        def install(adapter, package, deadline, reserve=0.0):
            installed.append((Path(package), reserve))
            return {}

        package = updater.repair_host_package(
            self.adapter, self.current, time.monotonic() + 120, install,
            lambda *_: {"idle": True, "service": "absent"},
        )
        self.assertIsNotNone(package)
        self.assertEqual(len(installed), 1)
        remote = package_manifest(package)["mcpServers"]["remote"]
        self.assertEqual(remote["toolTimeoutMs"], LEGACY_TIMEOUT_MS)
        self.assertEqual(remote["env"]["REMOTE_DEV_HARNESS_PROFILE"], "kimi")
        marker = json.loads(receipt_path.read_text(encoding="utf-8"))["host_package"]
        self.assertEqual(marker["remote_timeout_ms"], LEGACY_TIMEOUT_MS)
        self.assertEqual(marker["remote_profile"], "kimi")
        # A second check must not reinstall the same generation.
        self.assertIsNone(updater.repair_host_package(
            self.adapter, self.current, time.monotonic() + 120, install,
            lambda *_: {"idle": True, "service": "absent"},
        ))
        self.assertEqual(len(installed), 1)

    def test_failed_repair_build_keeps_previous_package_installed(self):
        self._legacy_receipt()
        package = self.generation / "host-package"
        write_json(package / "kimi.plugin.json", {"version": "0.1.3+legacy"})
        installs = []
        with patch.object(updater, "build_host_package", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                updater.repair_host_package(
                    self.adapter, self.current, time.monotonic() + 120,
                    lambda *a, **k: installs.append(a),
                    lambda *_: {"idle": True, "service": "absent"},
                )
        self.assertEqual(package_manifest(package)["version"], "0.1.3+legacy")
        self.assertEqual(installs, [])

    def test_idle_busy_keeps_previous_package_byte_identical(self):
        self._legacy_receipt()
        package = self.generation / "host-package"
        write_json(package / "kimi.plugin.json", {"version": "0.1.3+legacy"})
        sentinel = package / "sentinel.bin"
        sentinel.write_bytes(b"previous usable package")
        before = (package / "kimi.plugin.json").read_bytes(), sentinel.read_bytes()
        installs = []

        def busy(*_):
            raise updater.Deferred("service has actual active work")

        with self.assertRaises(updater.Deferred):
            updater.repair_host_package(
                self.adapter, self.current, time.monotonic() + 120,
                lambda *a, **k: installs.append(a), busy,
            )
        self.assertEqual((package / "kimi.plugin.json").read_bytes(), before[0])
        self.assertEqual(sentinel.read_bytes(), before[1])
        self.assertEqual(installs, [])
        self.assertEqual(list(self.generation.glob(".host-package-repair-*")), [])

    def test_busy_operation_lock_keeps_previous_package_byte_identical(self):
        self._legacy_receipt()
        package = self.generation / "host-package"
        write_json(package / "kimi.plugin.json", {"version": "0.1.3+legacy"})
        before = (package / "kimi.plugin.json").read_bytes()
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0, " + repr(str(SCRIPTS)) + ");"
             "import genstate;"
             "held = genstate.OperationLock().exclusive(timeout=5);"
             "held.__enter__(); print('held', flush=True); sys.stdin.readline()"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
            env=dict(os.environ, MINDIE_KIMI_CONFIG=str(self.config)),
        )
        for stream in (holder.stdin, holder.stdout):
            self.addCleanup(stream.close)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "held")
            installs = []
            with self.assertRaises(updater.LockTimeout):
                updater.repair_host_package(
                    self.adapter, self.current, time.monotonic() + 120,
                    lambda *a, **k: installs.append(a),
                    lambda *_: {"idle": True, "service": "absent"},
                    lock_timeout=0.2,
                )
            self.assertEqual(installs, [])
            self.assertEqual((package / "kimi.plugin.json").read_bytes(), before)
        finally:
            if holder.stdin is not None:
                holder.stdin.close()
            holder.wait(timeout=30)
        self.assertEqual(list(self.generation.glob(".host-package-repair-*")), [])

    def test_install_failure_restores_previous_package(self):
        receipt_path = self._legacy_receipt()
        package = self.generation / "host-package"
        write_json(package / "kimi.plugin.json", {"version": "0.1.3+legacy"})
        sentinel = package / "sentinel.bin"
        sentinel.write_bytes(b"previous usable package")
        before = (package / "kimi.plugin.json").read_bytes(), sentinel.read_bytes()

        def broken(*_a, **_k):
            raise RuntimeError("native API unreachable")

        with self.assertRaises(RuntimeError):
            updater.repair_host_package(
                self.adapter, self.current, time.monotonic() + 120, broken,
                lambda *_: {"idle": True, "service": "absent"},
            )
        self.assertEqual((package / "kimi.plugin.json").read_bytes(), before[0])
        self.assertEqual(sentinel.read_bytes(), before[1])
        self.assertNotIn("host_package", json.loads(receipt_path.read_text(encoding="utf-8")))
        self.assertFalse((self.generation / "host-package.repair-previous").exists())

    def test_install_failure_rolls_back_native_before_service_restore(self):
        receipt_path = self._legacy_receipt()
        package = self.generation / "host-package"
        package.mkdir()
        write_json(package / "kimi.plugin.json", {"name": "mindie-agent", "version": "0.1.3"})
        calls = []

        def install(_adapter, path, _deadline, reserve=0.0):
            calls.append(Path(path))
            if len(calls) == 1:
                raise RuntimeError("native API failed")
            return {"install": {"ok": True}}

        restored = []
        with patch.object(updater, "_restore_stopped_service",
                          side_effect=lambda adapter, final, deadline: restored.append(final)):
            with self.assertRaises(RuntimeError):
                updater.repair_host_package(
                    self.adapter, self.current, time.monotonic() + 120,
                    install, lambda *_: {"idle": True, "service": "stopped"},
                )
        self.assertEqual(len(calls), 2)
        self.assertEqual(restored, [self.current])
        self.assertTrue((package / "kimi.plugin.json").is_file())

    def test_check_repairs_legacy_receipt_before_reporting_current(self):
        self._legacy_receipt()
        installed = []

        def install(adapter, package, deadline, reserve=0.0):
            installed.append(Path(package))
            return {}

        selected = {
            "status": "up_to_date",
            "candidate": None,
            "selected_version": "0.1.3",
        }
        with patch.object(updater, "resolve_release", return_value=selected), \
                patch("component_update.check", return_value={"status": "up_to_date"}):
            code = updater._check_once(
                self.adapter, time.monotonic() + 120,
                force=False, build=None,
                idle=lambda *_: {"idle": True, "service": "absent"},
                install=install, lock_timeout=5,
            )
        self.assertEqual(code, 0)
        status = genstate.read_status(self.adapter)
        self.assertEqual(status["result"], "current")
        self.assertEqual(status["host_package_repair"], "installed")
        self.assertEqual(len(installed), 1)
        remote = package_manifest(installed[0])["mcpServers"]["remote"]
        self.assertEqual(remote["toolTimeoutMs"], LEGACY_TIMEOUT_MS)
        self.assertEqual(remote["env"]["REMOTE_DEV_HARNESS_PROFILE"], "kimi")

    def test_repaired_receipt_is_written_by_a_normal_switch(self):
        installs = []

        def install(adapter, package, deadline, reserve=0.0):
            installs.append(package)
            return {}

        updater.build_host_package(
            self.generation, self.adapter, SHA, config_file=self.config,
        )
        updater.switch(
            self.adapter, {**self.current, "sha": "a" * 40},
            self.generation, Path(sys.executable), self.config,
            SHA, time.monotonic() + 120,
            lambda *_: {"service": "absent"}, install, 1,
        )
        receipt = genstate.read_json(
            genstate.receipts_dir(self.adapter) / f"{SHA}.json")
        self.assertEqual(receipt["host_package"]["remote_timeout_ms"], LEGACY_TIMEOUT_MS)
        self.assertEqual(receipt["host_package"]["remote_profile"], "kimi")
        self.assertEqual(len(installs), 1)


class OldControllerSourceTests(unittest.TestCase):
    """Exercise the real 0.1.2/0.1.3 updater source, not a copied builder."""

    OLD = {
        "0.1.2": "7aecfd22857c313b52d6fe5923292f297884fec0",
        "0.1.3": "faf199fb7c76a63e9ff5f94cb1362ebdf203f5a3",
    }

    def setUp(self):
        if os.name == "nt":
            self.skipTest("the old package launcher contract is POSIX-only here")
        if not (ROOT / ".git").exists():
            self.skipTest("real old-controller source requires the repository history")
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.config = make_config(self.tmp / "cfg")
        self.adapter = json.loads(self.config.read_text())
        self.generation = self._candidate_generation()
        self.current = dict(
            generation=str(self.generation), python=sys.executable,
            adapter_config=str(self.config), sha=SHA,
        )
        genstate.write_current(self.current, self.adapter)

    def _extract(self, sha: str, destination: Path) -> Path:
        data = subprocess.run(
            ["git", "archive", "--format=tar", sha], cwd=ROOT,
            check=True, capture_output=True, timeout=60,
        ).stdout
        destination.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            archive.extractall(destination, filter="data")
        return destination

    def _candidate_generation(self) -> Path:
        generation = genstate.generations_dir(self.adapter) / SHA
        shutil.copytree(SCRIPTS, generation / "scripts")
        for name in ("skills", "commands"):
            shutil.copytree(ROOT / name, generation / name)
        shutil.copy2(ROOT / "kimi.plugin.json", generation / "kimi.plugin.json")
        return generation

    def _current_python(self) -> Path:
        pinned = os.environ.get("MINDIE_TEST_REMOTE_DEV")
        if not pinned:
            return Path(sys.executable)
        wrapper = self.tmp / "python"
        wrapper.write_text(
            "#!/bin/sh\nPYTHONPATH=" + shlex.quote(pinned)
            + " exec " + shlex.quote(sys.executable) + ' "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        return wrapper

    def _build_with(self, old_root: Path, generation: Path, *, mark=False) -> Path:
        code = (
            "import json,sys;from pathlib import Path;"
            "sys.path.insert(0,sys.argv[1]);import updater;"
            "adapter=json.loads(sys.argv[3]);"
            "package=updater.build_host_package(Path(sys.argv[2]),adapter,"
            "sys.argv[4],config_file=Path(sys.argv[5]));"
        )
        if mark:
            code += (
                "updater._mark_host_package(updater.receipts_dir(adapter)/"
                "(sys.argv[4]+'.json'),package);"
            )
        code += "print(package)"
        result = subprocess.run(
            [sys.executable, "-c", code, str(old_root / "scripts"),
             str(generation), json.dumps(self.adapter), SHA, str(self.config)],
            capture_output=True, text=True, timeout=60,
            env=dict(os.environ, MINDIE_KIMI_CONFIG=str(self.config)),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return Path(result.stdout.strip().splitlines()[-1])

    def _list_tools(self, package: Path, remote: dict) -> set:
        self.current["python"] = str(self._current_python())
        genstate.write_current(self.current, self.adapter)
        env = env_for(self.config)
        env.pop("REMOTE_DEV_HARNESS_PROFILE", None)
        process = subprocess.Popen(
            [str(package / remote["command"][2:]), *remote["args"]],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env, cwd=str(package),
        )
        self.addCleanup(process.kill)
        for stream in (process.stdin, process.stdout, process.stderr):
            self.addCleanup(stream.close)
        process.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
        )
        process.stdin.flush()
        reply = json.loads(process.stdout.readline())
        self.assertNotIn("error", reply, reply)
        names = {tool["name"] for tool in reply["result"]["tools"]}
        process.stdin.close()
        process.kill()
        process.wait(timeout=5)
        return names

    def test_real_old_builders_then_real_candidate_launcher(self):
        for version, sha in self.OLD.items():
            with self.subTest(version=version):
                old_root = self._extract(sha, self.tmp / f"old-{version}")
                package = self._build_with(old_root, self.generation)
                remote = package_manifest(package)["mcpServers"]["remote"]
                # Both immutable old builders rebuilt the entry from
                # command/args only, so the host timeout/profile were dropped.
                self.assertNotIn("env", remote)
                self.assertNotIn("toolTimeoutMs", remote)
                names = self._list_tools(package, remote)
                self.assertEqual(names, runtime_tool_names())
                self.assertTrue(NATIVE_NAMES <= names, names)
        # The candidate updater repairs the old-built package once.
        write_json(
            genstate.receipts_dir(self.adapter) / f"{SHA}.json",
            {"sha": SHA, "at": time.time(), "generation": str(self.generation)},
        )
        self.current["python"] = str(self._current_python())
        installed = []
        repaired = updater.repair_host_package(
            self.adapter, self.current, time.monotonic() + 120,
            lambda *a, **k: installed.append(a),
            lambda *_: {"idle": True, "service": "absent"},
        )
        remote = package_manifest(repaired)["mcpServers"]["remote"]
        self.assertEqual(remote["toolTimeoutMs"], LEGACY_TIMEOUT_MS)
        self.assertEqual(remote["env"]["REMOTE_DEV_HARNESS_PROFILE"], "kimi")
        self.assertEqual(len(installed), 1)

    def test_marked_two_surface_builder_repairs_components_once(self):
        old = self._extract("3813383c6b100294cb5ea4e9bd9d97a6ecfc1cae", self.tmp / "old-marked")
        receipt = genstate.receipts_dir(self.adapter) / f"{SHA}.json"
        write_json(receipt, {"sha": SHA, "generation": str(self.generation)})
        package = self._build_with(old, self.generation, mark=True)
        marker = genstate.read_json(receipt)["host_package"]
        self.assertNotIn("bound_mcp_surfaces", marker)
        raw = package_manifest(package)["mcpServers"]["components"]
        self.assertEqual(raw["args"][0], "./scripts/with_runtime.py")
        self.assertFalse((package / "scripts/with_runtime.py").exists())
        installed = []
        updater.switch(
            self.adapter, self.current, self.generation, Path(sys.executable),
            self.config, SHA, time.monotonic() + 180,
            lambda *_: {"idle": True, "service": "absent"},
            lambda *a, **k: installed.append(a), 1, update_native=False,
        )
        self.assertEqual(genstate.read_json(receipt)["host_package"], marker)
        self.assertEqual(installed, [])
        repaired = updater.repair_host_package(
            self.adapter, self.current, time.monotonic() + 30,
            lambda *a, **k: installed.append(a),
            lambda *_: {"idle": True, "service": "absent"},
        )
        names = self._list_tools(repaired, package_manifest(repaired)["mcpServers"]["components"])
        self.assertTrue({"profiler", "sim", "coordinator"} <= names, names)
        self.assertIsNone(updater.repair_host_package(
            self.adapter, self.current, time.monotonic() + 30,
            lambda *a, **k: installed.append(a),
        ))
        self.assertEqual(len(installed), 1)

    def test_old_build_runtime_override_gap_is_real(self):
        fake_bin = self.tmp / "fake-bin"
        fake_bin.mkdir()
        log = self.tmp / "uv.log"
        uv = fake_bin / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$UV_LOG"\n'
            'if [ "$1" = "venv" ]; then\n'
            '  for last in "$@"; do :; done\n'
            '  mkdir -p "$last/bin"\n'
            '  : > "$last/bin/python"\n'
            '  chmod +x "$last/bin/python"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        uv.chmod(0o755)
        code = (
            "import sys,time;from pathlib import Path;"
            "sys.path.insert(0,sys.argv[1]);import updater;"
            "updater.build_runtime(Path(sys.argv[2]),time.monotonic()+600)"
        )
        for version, sha in self.OLD.items():
            with self.subTest(version=version):
                old_root = self._extract(sha, self.tmp / f"argv-{version}")
                generation = self.tmp / f"requirements-{version}"
                generation.mkdir()
                shutil.copy2(ROOT / "runtime-requirements.txt",
                             generation / "runtime-requirements.txt")
                log.write_text("")
                result = subprocess.run(
                    [sys.executable, "-c", code, str(old_root / "scripts"),
                     str(generation)],
                    capture_output=True, text=True, timeout=60,
                    env=dict(
                        os.environ, PATH=str(fake_bin) + os.pathsep + os.environ["PATH"],
                        UV_LOG=str(log),
                    ),
                )
                self.assertNotEqual(result.returncode, 0)
                calls = log.read_text().splitlines()
                self.assertEqual(len(calls), 2, calls)
                self.assertEqual(calls[0].split()[0], "venv")
                pip = calls[1].split()
                self.assertEqual(pip[:3], ["pip", "install", "--python"])
                if version == "0.1.2":
                    self.assertNotIn("--overrides", pip)
                else:
                    self.assertIn("--overrides", pip)

    def test_candidate_recovery_build_uses_overrides(self):
        fake_bin = self.tmp / "recovery-bin"
        fake_bin.mkdir()
        log = self.tmp / "recovery-uv.log"
        uv = fake_bin / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            'printf "%s\\n" "$*" >> "$UV_LOG"\n'
            'if [ "$1" = "venv" ]; then\n'
            '  for last in "$@"; do :; done\n'
            '  mkdir -p "$last/bin"\n'
            '  : > "$last/bin/python"\n'
            '  chmod +x "$last/bin/python"\n'
            "  exit 0\n"
            "fi\n"
            "exit 1\n",
            encoding="utf-8",
        )
        uv.chmod(0o755)
        generation = self.tmp / "recovery-req"
        generation.mkdir()
        shutil.copy2(ROOT / "runtime-requirements.txt",
                     generation / "runtime-requirements.txt")
        with patch.dict(os.environ, {
            "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
            "UV_LOG": str(log),
        }):
            with self.assertRaises(RuntimeError):
                updater.build_runtime(generation, time.monotonic() + 600)
        calls = log.read_text().splitlines()
        self.assertEqual(len(calls), 2, calls)
        pip = calls[1].split()
        self.assertIn("--overrides", pip)
        self.assertEqual(pip[pip.index("--overrides") + 1],
                         str(generation / "runtime-requirements.txt"))


if __name__ == "__main__":
    unittest.main()
