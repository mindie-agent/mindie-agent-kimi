"""Native launch smoke test: spawn `kimi.plugin.json` MCP servers through Node.js."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parents[1]
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
import conftest  # noqa: E402

MANIFEST = REPO / "kimi.plugin.json"
KNOWLEDGE = {"knowledge_query", "knowledge_explain", "knowledge_feedback", "knowledge_distill", "knowledge_sync"}
REMOTE = {"remote_Read", "remote_Write", "remote_Edit", "remote_Bash", "remote_Glob", "remote_Grep",
          "remote_ReadMediaFile", "remote_TaskOutput", "remote_TaskStop", "remote_TaskList",
          "remote_auth", "remote_probe", "remote_artifact_pull", "remote_artifact_push"}

NODE_SCRIPT = r"""
const { spawn } = require("child_process");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const manifestPath = process.argv[2];
const surface = process.argv[3];
const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
const server = manifest.mcpServers[surface];
const command = server.command;
const cwd = path.resolve(process.env.MINDIE_TEST_PLUGIN_ROOT, server.cwd || ".");
const child = spawn(command, server.args, {
  cwd,
  env: { ...process.env, ...(server.env || {}) },
  windowsHide: true,
  shell: false,
  stdio: ["pipe", "pipe", "pipe"],
});
const lines = [];
let stderr = "";
const timer = setTimeout(() => { child.kill(); process.stderr.write("timeout\n"); process.exit(2); }, 60000);
readline.createInterface({ input: child.stdout }).on("line", (line) => {
  lines.push(line);
  if (lines.length === 4) {
    clearTimeout(timer);
    try { child.stdin.end(); } catch (_) {}
    child.kill();
    try {
      const replies = lines.map((item) => JSON.parse(item));
      const init = replies.find((item) => item.id === 1);
      const list = replies.find((item) => item.id === 2);
      const error = replies.find((item) => item.error && item.error.code === -32700);
      const ping = replies.find((item) => item.id === 3);
      process.stdout.write(JSON.stringify({
        command,
        version: init && init.result.serverInfo.version,
        tools: list && list.result.tools.map((tool) => tool.name),
        parse_error: error && error.error.code,
        ping: ping && ping.result,
      }));
      process.exit(0);
    } catch (error) {
      process.stderr.write(String(error) + "\n");
      process.exit(3);
    }
  }
});
child.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
child.on("error", (error) => { process.stderr.write(String(error) + "\n"); process.exit(4); });
child.on("exit", (code) => {
  if (lines.length < 4) {
    clearTimeout(timer);
    process.stderr.write("child exited early (" + code + "): " + stderr + "\n");
    process.exit(5);
  }
});
child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }) + "\n");
child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }) + "\n");
child.stdin.write("{not-json}\n");
child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: 3, method: "ping" }) + "\n");
"""


class NativeMCPLaunchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.node = shutil.which("node")
        if cls.node is None:
            raise unittest.SkipTest("Node.js is required for the native launch test")
        if shutil.which("uv") is None:
            raise unittest.SkipTest("uv is required for the native launch test")
        cls.root = Path(tempfile.mkdtemp(prefix="mindie-native-launch-"))
        cls.script = cls.root / "launch.js"
        cls.script.write_text(NODE_SCRIPT, encoding="utf-8")
        shim_py = conftest.create_subprocess_shim_venv(cls.root)
        cfg_dir = cls.root / "config"
        data_dir = cls.root / "data"
        home_dir = cls.root / "home"
        tmp_dir = cls.root / "tmp"
        for folder in (cfg_dir, data_dir, home_dir, tmp_dir):
            folder.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "kimi.json"
        cfg_path.write_text(json.dumps({"python": str(shim_py)}), encoding="utf-8")

        base_env = dict(os.environ)
        for drop in ("MINDIE_KIMI_BIN", "MINDIE_REDACTOR_EXECUTABLE", "MINDIE_KNOWLEDGE_ROOT"):
            base_env.pop(drop, None)
        py_bin_dir = str(Path(sys.executable).resolve().parent)
        cls.env = {
            **base_env,
            "PATH": py_bin_dir + os.pathsep + base_env.get("PATH", ""),
            "PYTHONUTF8": "1",
            "MINDIE_KIMI_SKIP_SCHTASKS": "1",
            "MINDIE_TEST_PLUGIN_ROOT": str(REPO),
            "HOME": str(home_dir),
            "USERPROFILE": str(home_dir),
            "APPDATA": str(home_dir / "AppData" / "Roaming"),
            "LOCALAPPDATA": str(home_dir / "AppData" / "Local"),
            "XDG_CONFIG_HOME": str(cfg_dir),
            "XDG_DATA_HOME": str(data_dir),
            "XDG_STATE_HOME": str(cls.root / "state"),
            "MINDIE_CONFIG_HOME": str(cfg_dir),
            "MINDIE_DATA_HOME": str(data_dir),
            "MINDIE_KIMI_CONFIG": str(cfg_path),
            "MINDIE_AGENT_CONFIG": str(cfg_path),
            "MINDIE_KNOWLEDGE_ROOT": str(data_dir / "knowledge"),
            "MINDIE_DIAGNOSTICS_ROOT": str(data_dir / "diagnostics"),
            "MINDIE_DIAGNOSTICS_CONFIG": str(cfg_dir / "diagnostics.json"),
            "KIMI_CODE_HOME": str(cls.root / "kimi-home"),
            "REMOTE_DEV_AUTH_DIR": str(cls.root / "remote-dev"),
            "UV_CACHE_DIR": str(tmp_dir / "uv-cache"),
            "TMPDIR": str(tmp_dir),
        }

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.root, ignore_errors=True)

    def _launch(self, surface: str) -> dict:
        result = subprocess.run(
            [self.node, str(self.script), str(MANIFEST), surface],
            cwd=str(REPO), env=self.env, capture_output=True, text=True, timeout=120,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_knowledge_surface(self) -> None:
        payload = self._launch("knowledge")
        self.assertEqual(payload["command"], "uv")
        self.assertEqual(payload["version"], "0.1.33")
        self.assertEqual(set(payload["tools"]), KNOWLEDGE)
        self.assertEqual(payload["parse_error"], -32700)
        self.assertEqual(payload["ping"], {})

    def test_remote_surface(self) -> None:
        payload = self._launch("remote")
        self.assertEqual(payload["command"], "uv")
        self.assertEqual(payload["version"], "0.1.33")
        self.assertEqual(set(payload["tools"]), REMOTE)
        self.assertEqual(payload["parse_error"], -32700)
        self.assertEqual(payload["ping"], {})

    def test_components_surface(self) -> None:
        payload = self._launch("components")
        self.assertEqual(payload["command"], "uv")
        self.assertEqual(payload["version"], "0.1.33")
        tools = set(payload["tools"])
        for expected in ("mindie_doctor", "mindie_status", "diagnostics_status", "profiler", "sim", "coordinator", "debugger"):
            self.assertIn(expected, tools)
        self.assertFalse([name for name in tools if name.startswith("knowledge_")])
        self.assertEqual(payload["parse_error"], -32700)
        self.assertEqual(payload["ping"], {})


if __name__ == "__main__":
    unittest.main()
