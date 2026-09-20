import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, SCRIPTS, env_for, make_config


DOCS_AGENT = (
    ROOT.parent / "kimi-upstream" / "docs" / "en" / "customization" / "agents.md"
)

SOURCE_TOML = """default_model = "kimi-code/k3"
builtin_product_skills = false

[providers."managed:kimi-code"]
type = "kimi"
base_url = "https://api.kimi.com/coding/v1"
api_key = ""

[models."kimi-code/k3"]
provider = "managed:kimi-code"
model = "k3"
max_context_size = 1048576
capabilities = [ "thinking", "always_thinking", "image_in", "video_in", "tool_use" ]
display_name = "K3"
support_efforts = [ "low", "high", "max" ]
default_effort = "max"

[thinking]
enabled = true
effort = "max"
"""


class AdmissionOrganizerTests(unittest.TestCase):
    def test_shared_admission_activate_and_claim_bool(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import admission as admission_mod

            try:
                from mindie_knowledge.loop.activation import Admission  # noqa: F401
            except ImportError as exc:
                self.fail(
                    "interpreter lacks canonical Admission; install "
                    "runtime-requirements (do not paper over with PYTHONPATH): "
                    + str(exc)
                )
            first = admission_mod.activate("ses_keep", project_root=str(tmp.resolve()))
            second = admission_mod.activate("ses_keep", project_root=str(tmp.resolve()))
            self.assertEqual(first["token"], second["token"])
            token = first["token"]
            gate = admission_mod.gate()
            self.assertIs(gate.claim("ses_keep", "stop", "turn-1", token=token), True)
            self.assertIs(gate.claim("ses_keep", "stop", "turn-1", token=token), False)
            gate.finish("ses_keep", token, True)

    def test_paused_activate_is_refused(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import admission as admission_mod

            lease = admission_mod.activate("ses_pause", project_root=str(tmp.resolve()))
            gate = admission_mod.gate()
            for _ in range(3):
                gate.finish("ses_pause", lease["token"], False)
            with self.assertRaises(ValueError) as caught:
                admission_mod.activate("ses_pause", project_root=str(tmp.resolve()))
            self.assertIn("paused", str(caught.exception).lower())

    def test_core_transcript_loader_not_a_local_copy(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            sys.path.insert(0, str(SCRIPTS))
            import knowledge_service

            module = knowledge_service.load_transcript()
            self.assertTrue(hasattr(module, "read_material"))
            self.assertTrue(hasattr(module, "FileIdentity"))
            self.assertTrue(hasattr(module, "identify"))

    def test_organizer_agent_disables_all_tools(self):
        text = (SCRIPTS / "organize-agent.md").read_text()
        self.assertIn("tools: []", text)
        self.assertIn("subagents: []", text)
        self.assertTrue(DOCS_AGENT.is_file())
        self.assertIn("`tools: []` disables all tools", DOCS_AGENT.read_text())

    def test_organizer_prompt_distinguishes_initial_from_verified(self):
        text = (SCRIPTS / "organize-agent.md").read_text()
        lowered = text.lower()
        self.assertIn("initial", lowered)
        self.assertIn("verified", lowered)
        self.assertIn("title", lowered)
        self.assertIn("summary", lowered)
        self.assertIn("uncertainty", lowered)
        self.assertIn("not a schema", lowered)
        self.assertIn("slogan", lowered)
        self.assertNotIn("physical_mapping_verified", text)
        self.assertNotIn("ASCEND_RT_VISIBLE_DEVICES", text)
        self.assertNotIn("atol", lowered)

    def test_organizer_missing_kimi_is_failure_not_empty_success(self):
        env = env_for(extra={"PATH": "/usr/bin:/bin", "MINDIE_KIMI_BIN": "/no/such/kimi"})
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "organizer.py")],
            input=json.dumps(dict(role="organize", increment="x")),
            text=True,
            capture_output=True,
            timeout=5,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotEqual(result.stdout.strip(), '{"entries": []}')

    def test_isolated_home_copies_provider_model_thinking_private(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            source = tmp / "source-home"
            source.mkdir()
            (source / "config.toml").write_text(SOURCE_TOML)
            os.environ["KIMI_CODE_HOME"] = str(source)
            sys.path.insert(0, str(SCRIPTS))
            import organizer

            isolated = organizer.prepare_isolated_home(tmp / "iso")
            config = isolated / "config.toml"
            self.assertTrue(config.is_file())
            mode = config.stat().st_mode
            self.assertEqual(stat.S_IMODE(mode), 0o600)
            text = config.read_text()
            self.assertIn("managed:kimi-code", text)
            self.assertIn("kimi-code/k3", text)
            self.assertIn("[\"thinking\"]", text)
            self.assertNotIn("[[hooks]]", text)
            self.assertFalse((isolated / "mcp.json").exists())
            installed = json.loads((isolated / "plugins" / "installed.json").read_text())
            self.assertEqual(installed.get("plugins"), [])
            report = organizer.doctor_isolated(isolated)
            self.assertTrue(isinstance(report, str))

    def test_isolated_home_missing_setup_is_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ["KIMI_CODE_HOME"] = str(tmp / "empty-home")
            (tmp / "empty-home").mkdir()
            sys.path.insert(0, str(SCRIPTS))
            import organizer

            with self.assertRaises(RuntimeError):
                organizer.prepare_isolated_home(tmp / "iso")
