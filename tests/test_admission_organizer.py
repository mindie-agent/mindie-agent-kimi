import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import ROOT, SCRIPTS, env_for, make_config


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

    def test_failure_counts_never_block_reactivate(self):
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
            again = admission_mod.activate("ses_pause", project_root=str(tmp.resolve()))
            self.assertEqual(again["token"], lease["token"])
            self.assertTrue(again["enabled"])

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

    def _source_home(self, tmp: Path) -> Path:
        source = tmp / "source-home"
        source.mkdir()
        (source / "config.toml").write_text(SOURCE_TOML)
        return source

    def _request(self, source: Path, **env_extra):
        import organizer
        from mindie_knowledge.materials import summarizer

        saved = os.environ.get("KIMI_CODE_HOME")
        saved_extra = {key: os.environ.get(key) for key in env_extra}
        os.environ["KIMI_CODE_HOME"] = str(source)
        os.environ.update(env_extra)
        try:
            identity = organizer.identity()
        finally:
            if saved is None:
                os.environ.pop("KIMI_CODE_HOME", None)
            else:
                os.environ["KIMI_CODE_HOME"] = saved
            for key, value in saved_extra.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        blocks = [
            dict(
                block_id=summarizer.digest(["block", index]),
                text=f"Public block {index} about NPU warmup.",
                source_range={"start": index, "end": index + 1},
            )
            for index in range(2)
        ]
        return summarizer.make_request(
            task_id=summarizer.digest(["task"]),
            body_version="v1",
            blocks=blocks,
            prior_navigation=None,
            identity=identity,
        )

    def _worker(self, payload, env):
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "organizer.py")],
            input=raw,
            text=True,
            capture_output=True,
            timeout=30,
            env=env,
        )

    def test_identity_is_fingerprinted_without_a_model_call(self):
        with tempfile.TemporaryDirectory() as raw:
            source = self._source_home(Path(raw))
            env = env_for(kimi_home=source, extra={"MINDIE_KIMI_BIN": "/no/such/kimi"})
            result = subprocess.run(
                [sys.executable, str(SCRIPTS / "organizer.py"), "--identity"],
                input="{}",
                text=True,
                capture_output=True,
                timeout=30,
                env=env,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            from mindie_knowledge.materials import summarizer

            identity = summarizer.validate_identity(json.loads(result.stdout))
            self.assertEqual(identity["model"], "k3")
            self.assertEqual(identity["effort"], "off")

    def test_missing_profile_is_a_configuration_failure_envelope(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            missing = tmp / "absent-home"
            request = self._request(self._source_home(tmp))
            env = env_for(kimi_home=missing, extra={"MINDIE_KIMI_BIN": "/no/such/kimi"})
            result = self._worker(request, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            from mindie_knowledge.materials import summarizer

            outcome = summarizer.validate_outcome(json.loads(result.stdout), request)
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["error"], "configuration")
            self.assertEqual(outcome["error_reason"], "configuration_invalid")
            self.assertEqual(outcome["model_calls"], 0)
            self.assertEqual(outcome["billing_status"], "not_called")

    def test_missing_kimi_is_a_start_failure_not_a_result(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            source = self._source_home(tmp)
            request = self._request(source, MINDIE_KIMI_BIN="/no/such/kimi")
            env = env_for(
                kimi_home=source,
                extra={"PATH": "/usr/bin:/bin", "MINDIE_KIMI_BIN": "/no/such/kimi"},
            )
            result = self._worker(request, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            from mindie_knowledge.materials import summarizer

            outcome = summarizer.validate_outcome(json.loads(result.stdout), request)
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["error"], "configuration")
            self.assertEqual(outcome["error_reason"], "native_start_failed")
            self.assertEqual(outcome["model_calls"], 0)
            self.assertEqual(outcome["billing_status"], "not_called")
            self.assertNotEqual(result.stdout.strip(), '{"entries": []}')

    def test_garbage_wire_input_is_rejected_without_an_envelope(self):
        env = env_for(extra={"PATH": "/usr/bin:/bin", "MINDIE_KIMI_BIN": "/no/such/kimi"})
        result = self._worker(json.dumps(dict(role="organize", increment="x")), env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertIn("invalid_result", result.stderr)

    def test_policy_drift_is_reported_not_served(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            source = self._source_home(tmp)
            request = self._request(source, MINDIE_KIMI_BIN="/no/such/kimi")
            from mindie_knowledge.materials import summarizer

            other = summarizer.policy_identity(model="other-model", effort="off", implementation={})
            drift = summarizer.make_request(
                task_id=request["task_id"],
                body_version=request["body_version"],
                blocks=request["blocks"],
                prior_navigation=None,
                identity=other,
            )
            env = env_for(kimi_home=source, extra={"MINDIE_KIMI_BIN": "/no/such/kimi"})
            result = self._worker(drift, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            outcome = summarizer.validate_outcome(json.loads(result.stdout), drift)
            self.assertEqual(outcome["status"], "failed")
            self.assertEqual(outcome["error"], "configuration")
            self.assertEqual(outcome["error_reason"], "worker_policy_changed")
            self.assertEqual(outcome["model_calls"], 0)

    def test_returned_outcome_envelope_round_trip(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            source = self._source_home(tmp)
            os.environ["KIMI_CODE_HOME"] = str(source)
            sys.path.insert(0, str(SCRIPTS))
            import organizer
            from mindie_knowledge.materials import summarizer

            request = self._request(source)
            result_payload = {
                "blocks": [
                    dict(block_id=block["block_id"], title="Block", summary="Observed.")
                    for block in request["blocks"]
                ],
                "navigation": {"title": "Navigation", "summary": "Observed."},
            }

            def fake_invoke(self, messages):
                from langchain_core.messages import AIMessage

                self.calls += 1
                self.receipt.update(native_started=True, turn_completed=True)
                self.raw_result = json.dumps(result_payload)
                return AIMessage(content=self.raw_result)

            saved = organizer.NativeKimiModel.invoke
            organizer.NativeKimiModel.invoke = fake_invoke
            try:
                outcome = organizer.run(request)
            finally:
                organizer.NativeKimiModel.invoke = saved
            validated = summarizer.validate_outcome(outcome, request)
            self.assertEqual(validated["status"], "returned")
            self.assertEqual(validated["model_calls"], 1)
            self.assertIs(validated["usage_known"], False)
            self.assertEqual(validated["billing_status"], "unknown")
            self.assertEqual(
                [block["block_id"] for block in validated["result"]["blocks"]],
                [block["block_id"] for block in request["blocks"]],
            )

    @unittest.skipUnless(os.name == "posix", "native stub wrapper needs a shell")
    def test_native_invocation_round_trip_with_stub_binary(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            source = self._source_home(tmp)
            stub = tmp / "kimi-stub"
            stub.write_text(
                "#!/bin/sh\n"
                'exec "' + sys.executable + '" "' + str(tmp / 'stub.py') + '" "$@"\n'
            )
            (tmp / "stub.py").write_text(
                "import json, re, sys\n"
                "args = sys.argv[1:]\n"
                "agent = args[args.index('--agent-file') + 1]\n"
                "text = open(agent, encoding='utf-8').read()\n"
                "seen = []\n"
                "for b in re.findall(r'\\\\?\"block_id\\\\?\":\\s*\\\\?\"([0-9a-f]{64})\\\\?\"', text):\n"
                "    if b not in seen:\n"
                "        seen.append(b)\n"
                "out = {'blocks': [{'block_id': b, 'title': 'Block', 'summary': 'Observed.'} for b in seen],\n"
                "       'navigation': {'title': 'Navigation', 'summary': 'Observed.'}}\n"
                "sys.stdout.write(json.dumps(out) + '\\n')\n"
            )
            stub.chmod(0o755)
            request = self._request(source, MINDIE_KIMI_BIN=str(stub))
            env = env_for(kimi_home=source, extra={"MINDIE_KIMI_BIN": str(stub)})
            result = self._worker(request, env)
            self.assertEqual(result.returncode, 0, result.stderr)
            from mindie_knowledge.materials import summarizer

            outcome = summarizer.validate_outcome(json.loads(result.stdout), request)
            self.assertEqual(outcome["status"], "returned")
            self.assertEqual(outcome["model_calls"], 1)
            self.assertEqual(
                [block["block_id"] for block in outcome["result"]["blocks"]],
                [block["block_id"] for block in request["blocks"]],
            )

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
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(mode), 0o600)
            text = config.read_text()
            self.assertIn("managed:kimi-code", text)
            self.assertIn("kimi-code/k3", text)
            self.assertIn("[\"thinking\"]", text)
            self.assertIn("\"enabled\" = false", text)
            import tomllib
            metadata = tomllib.loads(text)
            model = metadata['models']['kimi-code/k3']
            self.assertNotIn('thinking', model['capabilities'])
            self.assertNotIn('always_thinking', model['capabilities'])
            self.assertNotIn('default_effort', model)
            self.assertNotIn('support_efforts', model)
            self.assertEqual(metadata['thinking'], {'enabled': False})
            self.assertNotIn("[[hooks]]", text)
            self.assertFalse((isolated / "mcp.json").exists())
            installed = json.loads((isolated / "plugins" / "installed.json").read_text())
            self.assertEqual(installed.get("plugins"), [])
            # Native `kimi doctor config` is exercised in host acceptance.
            # This portable check covers only the configuration we write.

    def test_isolated_home_missing_setup_is_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ["KIMI_CODE_HOME"] = str(tmp / "empty-home")
            (tmp / "empty-home").mkdir()
            sys.path.insert(0, str(SCRIPTS))
            import organizer

            with self.assertRaises(organizer.ConfigurationError):
                organizer.prepare_isolated_home(tmp / "iso")
