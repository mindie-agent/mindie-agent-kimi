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

    def test_configured_init_read_only_activates_this_session(self):
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
            activation = payload.get("activation") or {}
            self.assertTrue(activation.get("enabled"))
            self.assertFalse(activation.get("paused"))
            self.assertEqual(activation.get("session"), "ses_cfg_ro")
            self.assertIn("service", activation)
            lease = admission.gate().active_lease("ses_cfg_ro")
            self.assertIsNotNone(lease)
            self.assertTrue(lease.get("enabled"))
            second = entry.op_init("ses_cfg_ro", str(tmp))
            self.assertTrue(second.get("already"))
            self.assertEqual(second.get("first_use"), "read-only")

    def test_configured_init_then_ordinary_choose_keeps_activation(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._configured_native_init(tmp, "ses_primary", "act-primary", "")
            self._stub_service()
            import admission
            import entry

            first = entry.op_init("ses_primary", str(tmp))
            self.assertTrue((first.get("activation") or {}).get("enabled"))
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

    def test_ordinary_user_choose_does_not_activate_when_configured(self):
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
            self.assertFalse((payload.get("this_session") or {}).get("activated", True))

    def test_configured_init_read_only_surfaces_paused(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._configured_native_init(tmp, "ses_paused", "act-paused", "read-only")
            self._stub_service()
            import admission
            import entry

            lease = admission.activate("ses_paused", project_root=str(tmp.resolve()))
            gate = admission.gate()
            for _ in range(3):
                gate.finish("ses_paused", lease["token"], False)
            payload = entry.op_init("ses_paused", str(tmp))
            self.assertEqual(payload.get("first_use"), "read-only")
            activation = payload.get("activation") or {}
            self.assertTrue(activation.get("paused"))
            self.assertFalse(activation.get("enabled"))
            self.assertIsNone(gate.active_lease("ses_paused"))

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
            self.assertTrue(payload["this_session"]["activated"])
            self.assertNotIn("active_leases", payload)
            self.assertNotIn("admission_path", payload)
            self.assertNotIn("recovery", payload)
