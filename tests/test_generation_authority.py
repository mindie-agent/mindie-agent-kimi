"""Authority paths never fork per update generation.

The active adapter config may be a generation fork after an updater flip;
consent, community settings, admission and capture authorization must still
resolve to the stable profile paths the bootstrap config uses, and state a
pre-fix fork stranded in a generation dir converges at the entry boundary.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

from support import (
    SCRIPTS,
    make_config,
    plugin_origin,
    stop_real_services,
    turn_records,
    write_json,
    write_session,
)
from test_independent_contract import _kill_wakes

sys.path.insert(0, str(SCRIPTS))
import updater  # noqa: E402

ENV_KEYS = (
    "HOME",
    "USERPROFILE",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_STATE_HOME",
    "KIMI_CODE_HOME",
    "MINDIE_KIMI_CONFIG",
    "MINDIE_DIAGNOSTICS_CONFIG",
    "MINDIE_DIAGNOSTICS_ROOT",
)


class GenerationAuthorityTests(unittest.TestCase):
    def setUp(self):
        self._scratch = tempfile.TemporaryDirectory(prefix="mindie-gen-authority-")
        self.tmp = Path(self._scratch.name)
        self._saved = {key: os.environ.get(key) for key in ENV_KEYS}
        home = self.tmp / "home"
        home.mkdir()
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(self.tmp / "diag-root")
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.tmp / "diagnostics.json")
        for name in (
            "entry", "entry_state", "consent", "paths", "sharing",
            "identity", "admission", "bridge", "transcript",
        ):
            sys.modules.pop(name, None)

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _kill_wakes(self.tmp)
        # A real spawned service outlives its fixture unless stopped
        # explicitly; on Windows its open handles otherwise pin the store.
        stop_real_services(self.tmp / "kimi.engine.json")
        self._scratch.cleanup()

    def _flipped(self, *, sharing=False):
        """A profile config plus a post-flip active generation config."""
        config = make_config(self.tmp, sharing=sharing, roots=[self.tmp])
        adapter = json.loads(config.read_text())
        generation = (
            self.tmp / "adapter-state" / "update" / "generations" / ("a" * 40)
        )
        (generation / "scripts").mkdir(parents=True)
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        gen_adapter = updater.write_generation_configs(
            generation, Path(sys.executable), adapter
        )
        os.environ["MINDIE_KIMI_CONFIG"] = str(gen_adapter)
        return config, gen_adapter, generation

    def _stub_service(self):
        import knowledge_service

        original = knowledge_service.ensure_service
        knowledge_service.ensure_service = lambda *_args, **_kwargs: None
        self.addCleanup(lambda: setattr(knowledge_service, "ensure_service", original))

    def test_generation_adapter_carries_stamped_kimi_bin(self):
        config = make_config(self.tmp, sharing=False)
        adapter = json.loads(config.read_text())
        adapter["kimi_bin"] = "/opt/kimi-stamped"
        generation = (
            self.tmp / "adapter-state" / "update" / "generations" / ("c" * 40)
        )
        (generation / "scripts").mkdir(parents=True)
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        gen_adapter = updater.write_generation_configs(
            generation, Path(sys.executable), adapter
        )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertEqual(gen_value["kimi_bin"], "/opt/kimi-stamped")

    def test_generation_adapter_reresolves_a_missing_kimi_bin(self):
        config = make_config(self.tmp, sharing=False)
        adapter = json.loads(config.read_text())
        home = self.tmp / "kimi-home"
        (home / "bin").mkdir(parents=True)
        binary = home / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")
        binary.write_text("# native binary\n")
        adapter["kimi_home"] = str(home)
        config.write_text(json.dumps(adapter, indent=2) + "\n")
        generation = (
            self.tmp / "adapter-state" / "update" / "generations" / ("d" * 40)
        )
        (generation / "scripts").mkdir(parents=True)
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        gen_adapter = updater.write_generation_configs(
            generation, Path(sys.executable), adapter
        )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertEqual(gen_value["kimi_bin"], str(binary))
        # Nothing resolvable: the key stays absent, never a guessed path.
        adapter.pop("kimi_home")
        config.write_text(json.dumps(adapter, indent=2) + "\n")
        empty = (
            self.tmp / "adapter-state" / "update" / "generations" / ("e" * 40)
        )
        (empty / "scripts").mkdir(parents=True)
        from unittest.mock import patch

        with patch("paths.shutil.which", return_value=None):
            gen_adapter = updater.write_generation_configs(
                empty, Path(sys.executable), adapter
            )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertNotIn("kimi_bin", gen_value)

    def test_generation_adapter_carries_and_reresolves_git_bin(self):
        config = make_config(self.tmp, sharing=False)
        adapter = json.loads(config.read_text())
        adapter["git_bin"] = "/opt/git-stamped"
        generation = (
            self.tmp / "adapter-state" / "update" / "generations" / ("f" * 40)
        )
        (generation / "scripts").mkdir(parents=True)
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        gen_adapter = updater.write_generation_configs(
            generation, Path(sys.executable), adapter
        )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertEqual(gen_value["git_bin"], "/opt/git-stamped")
        # Missing key: re-resolve deterministically (PATH hit), or stay
        # absent — never a guessed path.
        adapter.pop("git_bin")
        config.write_text(json.dumps(adapter, indent=2) + "\n")
        from unittest.mock import patch

        refill = (
            self.tmp / "adapter-state" / "update" / "generations" / ("0" * 40)
        )
        (refill / "scripts").mkdir(parents=True)
        with patch("paths.shutil.which", return_value="/usr/bin/git"):
            gen_adapter = updater.write_generation_configs(
                refill, Path(sys.executable), adapter
            )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertEqual(gen_value["git_bin"], "/usr/bin/git")
        empty = (
            self.tmp / "adapter-state" / "update" / "generations" / ("1" * 40)
        )
        (empty / "scripts").mkdir(parents=True)
        with patch("paths.shutil.which", return_value=None), \
             patch("paths.os.name", "posix"):
            gen_adapter = updater.write_generation_configs(
                empty, Path(sys.executable), adapter
            )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertNotIn("git_bin", gen_value)

    def test_generation_configs_keep_profile_authority_paths(self):
        config, gen_adapter, generation = self._flipped(sharing=True)
        adapter = json.loads(config.read_text())
        base_engine = json.loads(
            (self.tmp / "kimi.engine.json").read_text()
        )
        gen_value = json.loads(Path(gen_adapter).read_text())
        self.assertEqual(gen_value["profile_config"], str(config))
        self.assertEqual(gen_value["state_dir"], adapter["state_dir"])
        self.assertEqual(
            gen_value["community_config"], adapter["community_config"]
        )
        gen_engine = json.loads(Path(gen_value["engine_config"]).read_text())
        self.assertEqual(
            gen_engine["community_config"], base_engine["community_config"]
        )
        self.assertEqual(
            gen_engine["admission_path"], base_engine["admission_path"]
        )
        self.assertEqual(
            gen_engine["transcript_adapter"],
            str(generation / "scripts" / "transcript.py"),
        )
        # A pre-fix fork whose configs were repointed at a stranded file is
        # corrected from the profile configs, not inherited onward.
        stranded = generation / "config" / "mindie-community.json"
        write_json(stranded, {"schema": "mindie-community-config/1", "enabled": True})
        gen_value["community_config"] = str(stranded)
        Path(gen_adapter).write_text(json.dumps(gen_value, indent=2) + "\n")
        gen_engine["community_config"] = str(stranded)
        Path(gen_value["engine_config"]).write_text(
            json.dumps(gen_engine, indent=2) + "\n"
        )
        os.environ["MINDIE_KIMI_CONFIG"] = str(gen_adapter)
        next_generation = (
            self.tmp / "adapter-state" / "update" / "generations" / ("b" * 40)
        )
        (next_generation / "scripts").mkdir(parents=True)
        next_adapter = updater.write_generation_configs(
            next_generation, Path(sys.executable), gen_value
        )
        next_value = json.loads(Path(next_adapter).read_text())
        self.assertEqual(next_value["profile_config"], str(config))
        self.assertEqual(
            next_value["community_config"], adapter["community_config"]
        )
        next_engine = json.loads(Path(next_value["engine_config"]).read_text())
        self.assertEqual(
            next_engine["community_config"], base_engine["community_config"]
        )

    def test_choose_under_flipped_generation_writes_profile_authority(self):
        config, gen_adapter, generation = self._flipped(sharing=False)
        home = self.tmp / "kimi-home"
        write_session(
            home,
            "ses_flip",
            turn_records(
                plugin_origin("init", "act-flip", "contribute owner/repo alice"),
                "/mindie-agent contribute owner/repo alice",
            ),
            cwd=self.tmp,
        )
        os.environ["KIMI_CODE_HOME"] = str(home)
        self._stub_service()
        import consent
        import entry

        payload = entry.op_init("ses_flip", str(self.tmp))
        self.assertEqual(payload["first_use"], "contribute")
        # The consent document and community authority land at the PROFILE
        # directory, never inside the generation's config dir.
        self.assertTrue((self.tmp / "mindie-consent.json").is_file())
        self.assertEqual(consent.load()["choice"], "contribute")
        shared = json.loads((self.tmp / "mindie-community.json").read_text())
        self.assertTrue(shared["enabled"])
        self.assertEqual(shared["account"], "alice")
        self.assertFalse((generation / "config" / "mindie-consent.json").exists())
        self.assertFalse((generation / "config" / "mindie-community.json").exists())

    def test_handoff_and_service_read_one_generation(self):
        _config, gen_adapter, generation = self._flipped(sharing=True)
        del generation
        from mindie_knowledge.loop.store import Store

        Store(self.tmp / "domain", "vllm-ascend").close()
        import admission as admission_mod

        lease = admission_mod.activate(
            "ses_cap", project_root=str(self.tmp.resolve())
        )
        home = self.tmp / "kimi-home"
        write_session(
            home,
            "ses_cap",
            [
                dict(
                    type="context.append_message",
                    time=int((time.time() + 30) * 1000),
                    message=dict(
                        role="user",
                        content=[dict(type="text", text="public note")],
                        origin=dict(kind="user"),
                    ),
                )
            ],
            cwd=self.tmp,
        )
        gen_value = json.loads(Path(gen_adapter).read_text())
        gen_engine_path = gen_value["engine_config"]
        from mindie_knowledge.loop.cli import capture_hook, config_at
        from mindie_knowledge.loop import settings as settings_mod

        event = dict(
            hook_event_name="Stop",
            identity_kind="notification",
            session_id="ses_cap",
            mindie_activation=lease["token"],
            event_id="f" * 32,
            harness="kimi",
            transcript_path=str(
                home
                / "sessions"
                / "wd_test_abc"
                / "ses_cap"
                / "agents"
                / "main"
                / "wire.jsonl"
            ),
            cwd=str(self.tmp),
        )
        result = capture_hook(gen_engine_path, event)
        self.assertIn(result["stage"], {"accepted-local", "accepted"}, result)
        persisted = settings_mod.from_engine_config(
            config_at(gen_engine_path)
        ).generation
        profile_engine = json.loads((self.tmp / "kimi.engine.json").read_text())
        profile = settings_mod.from_engine_config(
            config_at(str(self.tmp / "kimi.engine.json"))
        ).generation
        self.assertEqual(persisted, profile)
        from mindie_knowledge.state_layout import state_root

        db = sqlite3.connect(
            state_root(self.tmp / "domain", "vllm-ascend") / "state-v4.sqlite3"
        )
        try:
            rows = list(db.execute("SELECT generation FROM captures"))
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], persisted)

    def _prefix_install(self):
        """A default-layout profile install plus pre-fix stranded forks."""
        xdg = self.tmp / "xdg-profile"
        profile_dir = xdg / "mindie-agent"
        config = make_config(profile_dir, sharing=False)
        os.environ["XDG_CONFIG_HOME"] = str(xdg)
        return config, profile_dir

    def _stranded_generation(self, profile_dir: Path, sha: str, files: dict):
        gen_dir = (
            profile_dir / "adapter-state" / "update" / "generations" / sha / "config"
        )
        gen_dir.mkdir(parents=True, exist_ok=True)
        adapter = json.loads((profile_dir / "kimi.json").read_text())
        # Pre-fix fork: no profile_config key, no stamped authority paths.
        adapter.pop("profile_config", None)
        write_json(gen_dir / "kimi.adapter.json", adapter)
        for name, value in files.items():
            path = gen_dir / name
            if isinstance(value, str):
                path.write_text(value)
            else:
                write_json(path, value)
        return gen_dir / "kimi.adapter.json"

    def test_stranded_consent_converges_at_entry_boundary(self):
        config, profile_dir = self._prefix_install()
        stranded_doc = {
            "schema": "mindie-consent/1",
            "choice": "contribute",
            "choice_at": 1,
            "reporting": "disabled",
            "reporting_at": 1,
        }
        active = self._stranded_generation(
            profile_dir, "1" * 40, {"mindie-consent.json": stranded_doc}
        )
        self._stranded_generation(
            profile_dir, "2" * 40, {"mindie-consent.json": "{broken-consent"}
        )
        os.environ["MINDIE_KIMI_CONFIG"] = str(active)
        import consent

        result = consent.adopt_stranded_consent()
        self.assertEqual(result["action"], "adopted", result)
        self.assertEqual(result["choice"], "contribute")
        self.assertEqual(result["reporting"], "disabled")
        self.assertTrue(result["notes"], result)
        profile_doc = profile_dir / "mindie-consent.json"
        self.assertEqual(json.loads(profile_doc.read_text()), stranded_doc)
        self.assertEqual(consent.load()["choice"], "contribute")
        # The stranded files stay as evidence; a second adoption is a no-op.
        self.assertTrue(
            (
                profile_dir
                / "adapter-state"
                / "update"
                / "generations"
                / ("1" * 40)
                / "config"
                / "mindie-consent.json"
            ).is_file()
        )
        self.assertEqual(consent.adopt_stranded_consent()["action"], "already")

    def test_conflicting_stranded_consent_is_diagnosed_not_guessed(self):
        config, profile_dir = self._prefix_install()
        active = self._stranded_generation(
            profile_dir,
            "1" * 40,
            {
                "mindie-consent.json": {
                    "schema": "mindie-consent/1",
                    "choice": "contribute",
                    "choice_at": 1,
                }
            },
        )
        self._stranded_generation(
            profile_dir,
            "2" * 40,
            {
                "mindie-consent.json": {
                    "schema": "mindie-consent/1",
                    "choice": "disabled",
                    "choice_at": 2,
                }
            },
        )
        os.environ["MINDIE_KIMI_CONFIG"] = str(active)
        import consent

        result = consent.adopt_stranded_consent()
        self.assertEqual(result["action"], "conflict", result)
        self.assertFalse((profile_dir / "mindie-consent.json").exists())
        self.assertEqual(consent.load()["state"], "missing")


if __name__ == "__main__":
    unittest.main()
