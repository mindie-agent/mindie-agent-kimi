import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from support import (
    SCRIPTS,
    env_for,
    make_config,
    plugin_origin,
    turn_records,
    write_session,
)

sys.path.insert(0, str(SCRIPTS))


class EntryTests(unittest.TestCase):
    def setUp(self):
        import importlib

        for name in ("entry", "entry_state", "identity", "paths"):
            sys.modules.pop(name, None)
        sys.path.insert(0, str(SCRIPTS))
        import entry  # noqa: F401
        importlib.reload(sys.modules["entry"])

    def _home(self, tmp, session, records, **kwargs):
        home = tmp / "kimi-home"
        write_session(home, session, records, cwd=tmp, **kwargs)
        os.environ["KIMI_CODE_HOME"] = str(home)
        return home

    def _configured_native_init(self, tmp, session, activation, args="read-only"):
        """Fixture: configured adapter + current /mindie-agent:init <args> turn."""
        config = make_config(tmp)
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        text = f"init {args}".strip() if args else "init"
        self._home(
            tmp,
            session,
            turn_records(plugin_origin("init", activation, args), text),
        )
        return config

    def _stub_service(self):
        import knowledge_service

        original = knowledge_service.ensure_service

        def boom(_engine=None):
            raise RuntimeError("unit-test: do not start knowledge service")

        knowledge_service.ensure_service = boom
        self.addCleanup(lambda: setattr(knowledge_service, "ensure_service", original))

    def test_unconfigured_init_shows_choices_and_consumes_activation_once(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ["MINDIE_KIMI_CONFIG"] = str(tmp / "absent.json")
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
            self._home(tmp, "ses_init", turn_records(plugin_origin("init", "act-once"), "init"))
            import entry

            first = entry.op_init("ses_init", str(tmp))
            self.assertFalse(first["configured"])
            self.assertEqual(len(first["choices"]), 3)
            self.assertFalse(first["sharing"]["enabled"])
            self.assertNotIn("active_leases", first)
            second = entry.op_init("ses_init", str(tmp))
            self.assertTrue(second.get("already"))

    def test_user_reply_choose_does_not_require_init_origin(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ["MINDIE_KIMI_CONFIG"] = str(tmp / "absent.json")
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
            self._home(tmp, "ses_user", turn_records(dict(kind="user"), "read-only"))
            import entry

            payload = entry.op_choose("ses_user", "read-only")
            self.assertEqual(payload["first_use"], "read-only")
            self.assertEqual(payload["choices"], [])
            records = turn_records(plugin_origin("init", "act-later"), "init")
            write_session(tmp / "kimi-home", "ses_again", records, cwd=tmp, workdir="wd_b")
            later = entry.dispatch("ses_again", str(tmp), "init")
            self.assertEqual(later.get("first_use"), "read-only")
            self.assertEqual(later.get("choices"), [])

    def test_native_init_read_only_args(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            os.environ["MINDIE_KIMI_CONFIG"] = str(tmp / "absent.json")
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
            self._home(
                tmp,
                "ses_ro",
                turn_records(plugin_origin("init", "act-ro", "read-only"), "init read-only"),
            )
            import entry

            payload = entry.op_init("ses_ro", str(tmp))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertEqual(payload["choices"], [])

    def test_configured_init_read_only_binds_this_session(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._configured_native_init(tmp, "ses_cfg_ro", "act-cfg-ro", "read-only")
            self._stub_service()
            import admission
            import entry

            payload = entry.op_init("ses_cfg_ro", str(tmp))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertEqual(payload["choices"], [])
            self.assertTrue(payload.get("repeat"))
            binding = payload.get("binding") or {}
            self.assertTrue(binding.get("enabled"))
            self.assertEqual(binding.get("session"), "ses_cfg_ro")
            self.assertIn("service", binding)
            lease = admission.gate().active_lease("ses_cfg_ro")
            self.assertIsNotNone(lease)
            self.assertTrue(lease.get("enabled"))
            second = entry.op_init("ses_cfg_ro", str(tmp))
            self.assertTrue(second.get("already"))
            self.assertEqual(second.get("first_use"), "read-only")

    def test_entry_skill_origin_binds_like_init(self):
        """The unified /mindie-agent skill origin performs the same binding."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            self._home(
                tmp,
                "ses_skill",
                turn_records(plugin_origin("mindie-agent", "act-skill", "read-only"),
                             "mindie-agent read-only"),
            )
            self._stub_service()
            import admission
            import entry

            payload = entry.op_init("ses_skill", str(tmp))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertTrue((payload.get("binding") or {}).get("enabled"))
            self.assertIsNotNone(admission.gate().active_lease("ses_skill"))

    def _skill_origin(self, name="mindie-agent", path=None, source="plugin",
                      activation="act-skill-real", args="", trigger="user-slash",
                      in_turn=False):
        from paths import PLUGIN_ROOT

        origin = dict(
            kind="skill_activation",
            activationId=activation,
            skillName=name,
            trigger=trigger,
            skillType="prompt",
            skillPath=path or str(PLUGIN_ROOT / "skills" / "mindie-agent" / "SKILL.md"),
            skillSource=source,
            skillArgs=args,
        )
        if in_turn:
            origin["inTurn"] = True
        return origin

    def test_real_skill_activation_origin_binds(self):
        """Kimi 2.x emits kind=skill_activation for /mindie-agent; it binds."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            origin = self._skill_origin(args="read-only")
            self._home(tmp, "ses_real", turn_records(origin, "/mindie-agent read-only"))
            self._stub_service()
            import admission
            import entry

            payload = entry.op_init("ses_real", str(tmp))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertTrue((payload.get("binding") or {}).get("enabled"))
            self.assertIsNotNone(admission.gate().active_lease("ses_real"))
            # The activationId is consumed once.
            again = entry.op_init("ses_real", str(tmp))
            self.assertTrue(again.get("already"))

    def test_print_mode_in_turn_activation_binds(self):
        """`kimi -p "/mindie-agent"`: the host resolves the prompt into an
        in-turn skill activation (trigger=model-tool); the user entry text
        makes it the real entry."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            origin = self._skill_origin(trigger="model-tool", source="extra",
                                        in_turn=True, args="read-only")
            records = turn_records(dict(kind="user"), "/mindie-agent read-only") + [
                dict(type="turn.steer", input=[dict(type="text", text="skill loaded")],
                     origin=origin, time=3),
            ]
            self._home(tmp, "ses_print", records)
            self._stub_service()
            import admission
            import entry

            payload = entry.op_init("ses_print", str(tmp))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertTrue((payload.get("binding") or {}).get("enabled"))
            self.assertIsNotNone(admission.gate().active_lease("ses_print"))

    def test_model_initiated_skill_use_is_not_the_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            origin = self._skill_origin(trigger="model-tool", source="extra",
                                        in_turn=True)
            records = turn_records(dict(kind="user"), "please fix the NPU bug") + [
                dict(type="turn.steer", input=[dict(type="text", text="skill loaded")],
                     origin=origin, time=3),
            ]
            self._home(tmp, "ses_self", records)
            import entry

            with self.assertRaises(ValueError):
                entry.op_init("ses_self", str(tmp))

    def test_same_named_foreign_skill_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            fake = tmp / "user-skills" / "mindie-agent" / "SKILL.md"
            fake.parent.mkdir(parents=True)
            fake.write_text("not the plugin skill")
            origin = self._skill_origin(path=str(fake))
            self._home(tmp, "ses_fake", turn_records(origin, "/mindie-agent"))
            import entry

            with self.assertRaises(ValueError):
                entry.op_init("ses_fake", str(tmp))

    def test_other_skill_activation_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            origin = self._skill_origin(name="other-skill")
            self._home(tmp, "ses_other", turn_records(origin, "/other-skill"))
            import entry

            with self.assertRaises(ValueError):
                entry.op_init("ses_other", str(tmp))

    def test_injected_origin_is_not_an_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            injected = dict(self._skill_origin(), kind="injection")
            records = turn_records(dict(kind="user"), "hello") + [
                dict(type="context.append_message",
                     message=dict(role="user", content=[dict(type="text", text="x")],
                                  origin=injected), time=3),
            ]
            self._home(tmp, "ses_inj", records)
            import entry

            with self.assertRaises(ValueError):
                entry.op_init("ses_inj", str(tmp))

    def test_configured_init_then_ordinary_choose_keeps_binding(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._configured_native_init(tmp, "ses_primary", "act-primary", "")
            self._stub_service()
            import admission
            import entry

            first = entry.op_init("ses_primary", str(tmp))
            self.assertTrue((first.get("binding") or {}).get("enabled"))
            self.assertIsNotNone(admission.gate().active_lease("ses_primary"))
            write_session(
                tmp / "kimi-home",
                "ses_primary",
                turn_records(dict(kind="user"), "read-only"),
                cwd=tmp,
            )
            chosen = entry.op_choose("ses_primary", "read-only")
            self.assertEqual(chosen["first_use"], "read-only")
            self.assertEqual(chosen["choices"], [])
            lease = admission.gate().active_lease("ses_primary")
            self.assertIsNotNone(lease)
            self.assertTrue(lease.get("enabled"))

    def test_ordinary_user_choose_does_not_bind_when_configured(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            self._home(tmp, "ses_noact", turn_records(dict(kind="user"), "read-only"))
            import admission
            import entry

            payload = entry.op_choose("ses_noact", "read-only")
            self.assertEqual(payload["first_use"], "read-only")
            self.assertIsNone(admission.gate().active_lease("ses_noact"))
            self.assertFalse((payload.get("this_session") or {}).get("bound", True))

    def test_failure_counts_never_pause_the_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._configured_native_init(tmp, "ses_failed", "act-failed", "read-only")
            self._stub_service()
            import admission
            import entry

            lease = admission.activate("ses_failed", project_root=str(tmp.resolve()))
            gate = admission.gate()
            for _ in range(5):
                gate.finish("ses_failed", lease["token"], False)
            payload = entry.op_init("ses_failed", str(tmp))
            self.assertEqual(payload.get("first_use"), "read-only")
            binding = payload.get("binding") or {}
            self.assertTrue(binding.get("enabled"))
            self.assertIsNotNone(gate.active_lease("ses_failed"))

    def test_sharing_enable_uses_native_args_not_model_args(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project = tmp / "proj"
            project.mkdir()
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            args = (
                f"--repository owner/repo --account acc --project-root {project} "
                "--visibility public"
            )
            self._home(
                tmp,
                "ses_share",
                turn_records(plugin_origin("sharing-enable", "act-share", args), "enable"),
            )
            import entry

            result = entry.op_sharing_enable(
                "ses_share",
                "--repository evil/repo --account other --project-root /tmp --visibility public",
            )
            self.assertTrue(result.get("enabled"))
            self.assertEqual(result.get("repository"), "owner/repo")

    def test_configured_status_is_this_session_only(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            self._home(tmp, "ses_st", turn_records(plugin_origin("status", "act-st"), "status"))
            import admission
            import entry

            admission.activate("ses_st", project_root=str(tmp.resolve()))
            admission.activate("ses_other", project_root=str(tmp.resolve()))
            payload = entry.op_status("ses_st")
            self.assertTrue(payload["configured"])
            self.assertIn("this_session", payload)
            self.assertTrue(payload["this_session"]["bound"])
            self.assertNotIn("active_leases", payload)
            self.assertNotIn("admission_path", payload)
            self.assertNotIn("recovery", payload)

    def test_conversational_contribute_enables_current_project(self):
        """First contribution without learning a CLI: the user names the
        public repository and account in an ordinary reply."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            self._home(
                tmp, "ses_contrib",
                turn_records(dict(kind="user"), "contribute to owner/repo as acc-name"),
            )
            import consent
            import entry

            payload = entry.op_choose(
                "ses_contrib", "contribute", str(tmp),
                repository="owner/repo", account="acc-name",
            )
            self.assertEqual(payload["first_use"], "contribute")
            self.assertTrue(payload["sharing"]["enabled"])
            self.assertEqual(payload["sharing"]["repository"], "owner/repo")
            saved = consent.load()
            self.assertEqual(saved["choice"], "contribute")
            # The entry boundary converged settings onto the shared authority.
            settings = json.loads(consent.shared_community_path().read_text())
            self.assertTrue(settings["enabled"])
            self.assertEqual(settings["project_roots"], [str(tmp.resolve())])
            self.assertEqual(settings["consent_config"], str(consent.consent_path()))

    def test_conversational_contribute_destination_must_be_users_words(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            self._home(
                tmp, "ses_evil",
                turn_records(dict(kind="user"), "yes, enable contribution please"),
            )
            import consent
            import entry

            with self.assertRaises(ValueError):
                entry.op_choose(
                    "ses_evil", "contribute", str(tmp),
                    repository="evil/repo", account="acc-name",
                )
            self.assertIsNone(consent.load()["choice"])
            settings = json.loads((tmp / "kimi.community.json").read_text())
            self.assertFalse(settings["enabled"])

    def test_native_contribute_args_enable_sharing(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            origin = self._skill_origin(args="contribute owner/repo acc-name")
            self._home(tmp, "ses_natc", turn_records(origin, "/mindie-agent contribute owner/repo acc-name"))
            self._stub_service()
            import consent
            import entry

            payload = entry.op_init("ses_natc", str(tmp))
            self.assertEqual(payload["first_use"], "contribute")
            self.assertTrue(payload["sharing"]["enabled"])
            self.assertEqual(consent.load()["choice"], "contribute")
            settings = json.loads(consent.shared_community_path().read_text())
            self.assertEqual(settings["account"], "acc-name")
            self.assertEqual(settings["project_roots"], [str(tmp.resolve())])

    def test_user_reply_cannot_change_a_saved_choice(self):
        """A saved choice is changed only through the entry, never by a
        model-initiated choose on an ordinary mention turn."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            import consent
            import entry

            consent.record_choice("read-only")
            self._home(tmp, "ses_locked", turn_records(dict(kind="user"), "what is later?"))
            with self.assertRaises(ValueError):
                entry.op_choose("ses_locked", "later", str(tmp))
            self.assertEqual(consent.load()["choice"], "read-only")

    def test_read_only_choice_disables_existing_sharing(self):
        """Revocation is real: choosing read-only via the entry turns the
        sharing settings off instead of leaving an enabled file behind."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            project = tmp / "proj"
            project.mkdir()
            config = make_config(tmp, sharing=True, roots=[project])
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            self._home(
                tmp, "ses_revoke",
                turn_records(plugin_origin("init", "act-ro", "read-only"), "init read-only"),
            )
            self._stub_service()
            import consent
            import entry

            payload = entry.op_init("ses_revoke", str(project))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertFalse(payload["sharing"]["enabled"])
            self.assertEqual(consent.load()["choice"], "read-only")
            settings = json.loads(consent.shared_community_path().read_text())
            self.assertFalse(settings["enabled"])

    def test_reporting_choice_recorded_from_first_setup_conversation(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            os.environ["XDG_CONFIG_HOME"] = str(tmp / "xdg")
            self._home(
                tmp, "ses_rep",
                turn_records(dict(kind="user"), "read-only, and no reporting"),
            )
            from unittest.mock import patch

            import consent
            import diagnostic_support
            import entry

            with patch.object(
                diagnostic_support, "configure_reporting",
                return_value={"enabled": False},
            ) as configure:
                payload = entry.op_choose(
                    "ses_rep", "read-only", str(tmp), reporting="disabled",
                )
            configure.assert_called_once_with(False, sys.executable)
            self.assertEqual(payload["first_use"], "read-only")
            saved = consent.load()
            self.assertEqual(saved["choice"], "read-only")
            self.assertEqual(saved["reporting"], "disabled")

    def test_in_turn_prefix_trap_is_not_the_entry(self):
        """Regression: '/mindie-agentuous …' is a plain mention, not an
        entry invocation of the in-turn model-tool form."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            origin = self._skill_origin(trigger="model-tool", source="extra",
                                        in_turn=True)
            records = turn_records(dict(kind="user"), "/mindie-agentuous please") + [
                dict(type="turn.steer", input=[dict(type="text", text="skill loaded")],
                     origin=origin, time=3),
            ]
            self._home(tmp, "ses_prefix", records)
            import entry

            with self.assertRaises(ValueError):
                entry.op_init("ses_prefix", str(tmp))

    def test_entry_boundary_adopts_legacy_marker_choice_once(self):
        """The entry invocation performs the one-time legacy import; a
        second session simply reuses the saved choice."""
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            config = make_config(tmp)
            os.environ["MINDIE_KIMI_CONFIG"] = str(config)
            from paths import first_use_path

            first_use_path().parent.mkdir(parents=True, exist_ok=True)
            first_use_path().write_text(json.dumps({"choice": "read-only"}))
            self._home(
                tmp, "ses_legacy",
                turn_records(plugin_origin("init", "act-legacy", ""), "init"),
            )
            self._stub_service()
            import consent
            import entry

            payload = entry.op_init("ses_legacy", str(tmp))
            self.assertEqual(payload["first_use"], "read-only")
            self.assertEqual(payload["choices"], [])
            migration = payload.get("migration") or {}
            self.assertEqual((migration.get("consent") or {}).get("action"), "imported")
            self.assertEqual(consent.load()["choice"], "read-only")
