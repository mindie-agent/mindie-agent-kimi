"""Native stdio launch contract; run explicitly with Node.js on PATH.

Uses the same shell=False process boundary as the Kimi MCP host. No model,
credentials, network, fake interpreter, or mock subprocess is involved.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS))
from support import SCRIPTS, make_config
sys.path.insert(0, str(SCRIPTS))
import updater
import genstate

NODE_SPAWN = r"""
const fs = require('node:fs');
const {spawnSync} = require('node:child_process');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = spawnSync(input.command, input.args, {
  cwd: input.cwd, env: {...process.env, ...input.env},
  shell: false, encoding: 'utf8', timeout: 10000, windowsHide: true,
});
if (result.error) { console.error(result.error.code); process.exit(1); }
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
process.exit(result.status === null ? 1 : result.status);
"""

NODE_MCP = r"""
const fs=require('node:fs'), {spawn}=require('node:child_process'), readline=require('node:readline');
const spec=JSON.parse(fs.readFileSync(0,'utf8'));
const child=spawn(spec.command,spec.args,{cwd:spec.cwd,env:{...process.env,...spec.env},shell:false,windowsHide:true});
let output='', errors='', fault=null;
const timer=setTimeout(()=>{fault='timeout';child.kill();},10000);
const send=row=>child.stdin.write(JSON.stringify(row)+'\n');
child.on('error',e=>{fault=e.code;});
child.stderr.on('data',data=>{errors+=data;});
readline.createInterface({input:child.stdout}).on('line',line=>{
  output+=line+'\n';
  const reply=JSON.parse(line);
  if(reply.id===1){
    send({jsonrpc:'2.0',method:'notifications/initialized'});
    send({jsonrpc:'2.0',id:2,method:'tools/list',params:{}});
  }
  if(reply.id===2)child.stdin.end();
});
child.on('close',code=>{clearTimeout(timer);process.stdout.write(JSON.stringify({code,error:fault,stdout:output,stderr:errors}));});
send({jsonrpc:'2.0',id:1,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'mindie-native-contract',version:'1'}}});
"""


class NativeMcpLaunchTests(unittest.TestCase):
    def test_generated_package_lists_real_runtime_tools(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "native launch contract requires Node.js on PATH")
        with tempfile.TemporaryDirectory(prefix="mindie native runtime ") as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            for name in ("mindie_launch.py", "bounded.py", "diagnostic_support.py",
                         "diagnostic_fallback.py"):
                shutil.copy2(SCRIPTS / name, source / "scripts" / name)
            shutil.copy2(TESTS.parent / "kimi.plugin.json", source / "kimi.plugin.json")
            config = make_config(root / "config")
            adapter = json.loads(config.read_text())
            sha = "b" * 40
            package = updater.build_host_package(source, adapter, sha, config_file=config)
            genstate.write_current(dict(generation=str(TESTS.parent), python=sys.executable,
                                        adapter_config=str(config), sha=sha), adapter)
            manifest = json.loads((package / "kimi.plugin.json").read_text())
            expected = {"knowledge": {"mindie_entry", "knowledge_query", "knowledge_explain"},
                        "remote": {"remote_read", "remote_bash", "remote_job_status"}}
            for surface, server in manifest["mcpServers"].items():
                with self.subTest(surface=surface):
                    spec = dict(server, cwd=str(package))
                    spec.setdefault("env", {})
                    process = subprocess.run([node, "-e", NODE_MCP], input=json.dumps(spec),
                                             text=True, encoding="utf-8", capture_output=True, timeout=15)
                    self.assertEqual(process.returncode, 0, process.stderr)
                    result = json.loads(process.stdout)
                    self.assertEqual(result["code"], 0, result)
                    self.assertIsNone(result["error"], result)
                    replies = [json.loads(line) for line in result["stdout"].splitlines()]
                    listing = next((row for row in replies if row.get("id") == 2), {})
                    self.assertNotIn("error", listing, listing)
                    names = {tool["name"] for tool in listing.get("result", {}).get("tools", [])}
                    self.assertTrue(expected[surface] <= names, listing)

    def test_generated_manifest_launches_exact_interpreter_and_arguments(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "native launch contract requires Node.js on PATH")
        with tempfile.TemporaryDirectory(prefix="mindie launch ") as temporary:
            root = Path(temporary) / "路径 & $literal %value%"
            root.mkdir()
            source = root / "source"
            (source / "scripts").mkdir(parents=True)
            repository = TESTS.parent
            for name in ("mindie_launch.py", "bounded.py", "diagnostic_support.py",
                         "diagnostic_fallback.py"):
                shutil.copy2(repository / "scripts" / name, source / "scripts" / name)
            shutil.copy2(repository / "kimi.plugin.json", source / "kimi.plugin.json")
            sha = "a" * 40
            # The child is a real interpreter. Echo only makes the executed
            # executable/argv observable; packaging and spawning stay real.
            (source / "scripts/mindie_launch.py").write_text(
                "import json, sys\nprint(json.dumps({'python': sys.executable, 'args': sys.argv[1:]}))\n",
                encoding="utf-8",
            )
            config = make_config(root / "config")
            adapter = json.loads(config.read_text())
            package = updater.build_host_package(source, adapter, sha, config_file=config)
            manifest = json.loads((package / "kimi.plugin.json").read_text())
            for surface, server in manifest["mcpServers"].items():
                with self.subTest(surface=surface):
                    spec = dict(server, cwd=str(package))
                    spec.setdefault("env", {})
                    result = subprocess.run(
                        [node, "-e", NODE_SPAWN], input=json.dumps(spec),
                        text=True, encoding="utf-8", capture_output=True, timeout=20,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    reply = json.loads(result.stdout)
                    self.assertEqual(Path(reply["python"]), Path(sys.executable))
                    self.assertEqual(reply["args"], ["--config", str(config), "mcp", surface])


if __name__ == "__main__":
    unittest.main()
