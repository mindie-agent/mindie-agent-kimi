import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, env_for, make_config


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
            self.assertIs(admission_mod.claim("ses_keep", "stop", "turn-1", token=token), True)
            self.assertIs(admission_mod.claim("ses_keep", "stop", "turn-1", token=token), False)
            admission_mod.finish("ses_keep", token, True)

    def test_organizer_agent_disables_all_tools(self):
        text = (SCRIPTS / "organize-agent.md").read_text()
        self.assertIn("tools: []", text)
        self.assertIn("subagents: []", text)

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
