"""Regression tests for WSL2 binary/DrvFS hazards and Windows native launch parity."""

from __future__ import annotations

import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import install_kimi_plugin
import mindie_launch
import organizer
import paths
import updater_schedule
import with_runtime


def _write_fake_pe(path: Path) -> None:
    buf = bytearray(128)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, 64)
    buf[64:68] = b"PE\x00\x00"
    path.write_bytes(bytes(buf))
    path.chmod(0o755)


class WslAndWindowsParityTests(unittest.TestCase):
    def test_pe_and_drvfs_linux_vs_windows_shim_classification(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            pe_file = root / "kimi_pe"
            _write_fake_pe(pe_file)
            linux_venv_script = root / "mindie-profiler"
            linux_venv_script.write_text("#!/mnt/c/proj/.venv/bin/python\nimport sys\n", encoding="utf-8")
            linux_venv_script.chmod(0o755)
            npm_shim = root / "kimi-npm"
            npm_shim.write_text('#!/bin/sh\nbasedir=$(cygpath -w "$0")\nexec "$basedir/kimi.exe"\n', encoding="utf-8")
            npm_shim.chmod(0o755)
            elf_bin = root / "elf-bin"
            elf_bin.write_bytes(b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 56)
            elf_bin.chmod(0o755)

            self.assertTrue(paths._has_pe_header(pe_file))
            self.assertFalse(paths._has_pe_header(linux_venv_script))
            self.assertTrue(paths._is_wsl_linux_executable(linux_venv_script))
            self.assertTrue(paths._is_wsl_linux_executable(elf_bin))
            self.assertFalse(paths._is_wsl_linux_executable(npm_shim))
            self.assertFalse(paths._is_wsl_linux_executable(pe_file))

            if os.name == "posix":
                pe_dir = root / "win_shim"
                linux_dir = root / "linux_bin"
                pe_dir.mkdir()
                linux_dir.mkdir()
                pe_kimi = pe_dir / "kimi"
                _write_fake_pe(pe_kimi)
                linux_kimi = linux_dir / "kimi"
                linux_kimi.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                linux_kimi.chmod(0o755)

                combined_path = f"/mnt/c/Users/demo/.kimi-code/bin{os.pathsep}{pe_dir}{os.pathsep}{linux_dir}"
                with patch.dict(os.environ, {"WSL_DISTRO_NAME": "Ubuntu-24.04", "PATH": combined_path}):
                    self.assertTrue(paths.in_wsl())
                    self.assertTrue(paths.is_windows_host_binary("/mnt/c/Users/demo/.kimi-code/bin/kimi"))
                    self.assertTrue(paths.is_windows_host_binary(pe_kimi))
                    self.assertFalse(paths.is_windows_host_binary(linux_kimi))
                    self.assertEqual(paths.which_host_binary("kimi"), str(linux_kimi))
                    with patch.object(paths.shutil, "which", lambda _name: "/mnt/c/Users/demo/.kimi-code/bin/kimi"):
                        self.assertEqual(paths.which_host_binary("kimi", which_fn=paths.shutil.which), str(linux_kimi))
                    os.environ.pop("MINDIE_KIMI_BIN", None)
                    self.assertEqual(
                        paths.resolve_kimi_bin({"kimi_home": "/mnt/c/Users/demo/.kimi-code"}),
                        str(linux_kimi),
                    )
            else:
                win_apps = root / "Microsoft" / "WindowsApps"
                real_bin = root / "real_bin"
                win_apps.mkdir(parents=True)
                real_bin.mkdir(parents=True)
                (win_apps / "python.exe").write_bytes(b"")
                real_py = real_bin / "python.exe"
                _write_fake_pe(real_py)
                with patch.dict(os.environ, {"PATH": f"{win_apps}{os.pathsep}{real_bin}"}):
                    resolved = paths.which_host_binary("python")
                    self.assertIsNotNone(resolved)
                    self.assertEqual(Path(resolved).resolve(), real_py.resolve())

    def test_doctor_isolated_allows_drvfs_0777_but_rejects_linux_ext4_world_readable(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "isolated"
            home.mkdir()
            cfg = home / "config.toml"
            cfg.write_text('[providers."managed:kimi-code"]\nkey = "x"\n', encoding="utf-8")
            cfg.chmod(0o644)

            with patch("paths.is_windows_or_wsl_drvfs", return_value=False):
                if os.name == "posix":
                    with self.assertRaisesRegex(RuntimeError, "not private"):
                        organizer.doctor_isolated(home)

            with patch("paths.is_windows_or_wsl_drvfs", return_value=True), \
                 patch.object(organizer, "kimi_bin", return_value="/usr/bin/kimi"), \
                 patch.object(organizer.bounded, "run", return_value="ok") as run_mock:
                self.assertEqual(organizer.doctor_isolated(home), "ok")
                run_mock.assert_called_once()

    def test_resolve_git_bin_discovers_user_scoped_git_even_with_percent_in_path(self):
        with tempfile.TemporaryDirectory() as raw:
            local_app = Path(raw) / "100%" / "AppData" / "Local"
            git_exe = local_app / "Programs" / "Git" / "cmd" / "git.exe"
            git_exe.parent.mkdir(parents=True)
            git_exe.write_bytes(b"MZ")
            fake_os = types.SimpleNamespace(name="nt", environ=os.environ, path=os.path)
            with patch.dict(os.environ, {"LOCALAPPDATA": str(local_app)}, clear=False), \
                 patch("paths.shutil.which", return_value=None), \
                 patch("paths.os", fake_os), \
                 patch(
                     "paths.GIT_WINDOWS_LOCATIONS",
                     (
                         r"%MINDIE_UNSET_PLACEHOLDER%\Git\cmd\git.exe",
                         r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe",
                     ),
                 ):
                os.environ.pop("MINDIE_KIMI_GIT_BIN", None)
                os.environ.pop("MINDIE_UNSET_PLACEHOLDER", None)
                import importlib
                active_paths = importlib.import_module("paths")
                self.assertIsNone(active_paths._expand_git_location(r"%MINDIE_UNSET_PLACEHOLDER%\Git\cmd\git.exe"))
                found = active_paths.resolve_git_bin()
                self.assertIsNotNone(found)
                self.assertEqual(Path(found), git_exe)

    def test_installer_fails_closed_when_host_kimi_discovery_rejects_all_candidates(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw) / "kimi-home"
            plugin_src = Path(raw) / "plugin"
            plugin_src.mkdir()
            with patch.object(
                sys, "argv",
                ["install_kimi_plugin.py", "--kimi-home", str(home), "--plugin-root", str(plugin_src)],
            ), patch.dict(os.environ, {}, clear=False), \
                 patch("paths.which_host_binary", return_value=None), \
                 patch.object(install_kimi_plugin, "KIMI", "kimi"), \
                 patch.object(install_kimi_plugin.subprocess, "Popen") as popen_mock:
                os.environ.pop("MINDIE_KIMI_BIN", None)
                with self.assertRaises(SystemExit) as ctx:
                    install_kimi_plugin.main()
                self.assertIn("FileNotFoundError", str(ctx.exception.code))
                popen_mock.assert_not_called()

    def test_same_interpreter_and_bom_config_across_launcher_and_paths(self):
        fake_os = types.SimpleNamespace(name="nt", environ=os.environ, path=os.path, fspath=os.fspath)
        import importlib
        active_paths = importlib.import_module("paths")
        with patch.object(active_paths, "os", fake_os):
            self.assertTrue(
                active_paths.same_interpreter(
                    r"\\?\c:\Users\中文\venv\Scripts\python.exe",
                    r"C:\Users\中文\venv\Scripts\PYTHON.EXE",
                )
            )
        with tempfile.TemporaryDirectory() as raw:
            import json

            cfg = Path(raw) / "kimi.json"
            state = Path(raw) / "custom-state"
            cfg.write_bytes(
                b"\xef\xbb\xbf" + json.dumps({"python": "C:/venv/python.exe", "state_dir": str(state)}).encode("utf-8")
            )
            with patch.dict(os.environ, {"MINDIE_KIMI_CONFIG": str(cfg)}):
                self.assertEqual(active_paths.load_adapter_config()["state_dir"], str(state))
                self.assertEqual(mindie_launch._state_dir(), state)

    def test_verify_scheduled_task_strips_utf8_bom_and_uses_open_standard_output(self):
        from xml.sax.saxutils import escape

        wrapper = Path(r"C:\Users\中文 & 100%\MindIEKimiUpdate.pyw")
        pythonw = Path(r"C:\Users\中文 & 100%\pythonw.exe")
        xml_payload = (
            "\ufeff<Task><Actions><Exec>"
            f"<Command>{escape(str(pythonw))}</Command>"
            f"<Arguments>{escape(subprocess.list2cmdline([str(wrapper)]))}</Arguments>"
            "</Exec></Actions></Task>"
        )
        seen_cmd = []

        def fake_run(cmd, *_args, **_kwargs):
            seen_cmd.append(cmd)
            return xml_payload

        with patch.object(updater_schedule, "bounded_run", side_effect=fake_run), \
             patch.object(updater_schedule, "_HOST", None):
            updater_schedule._verify_scheduled_task(wrapper, pythonw)
        self.assertEqual(seen_cmd[0][0], "powershell")
        self.assertIn("OpenStandardOutput", seen_cmd[0][-1])
        self.assertNotIn("[Console]::OutputEncoding", seen_cmd[0][-1])

    def test_windows_reexec_waits_for_child_and_forwards_stdio(self):
        with tempfile.TemporaryDirectory() as raw:
            cfg = Path(raw) / "kimi.json"
            cfg.write_bytes(b'\xef\xbb\xbf{"python": "C:/venv/Scripts/python.exe"}')
            script = Path(raw) / "target.py"
            script.write_text("print('ok')\n", encoding="utf-8")
            done = subprocess.CompletedProcess(["C:/venv/Scripts/python.exe"], 17)
            fake_os = types.SimpleNamespace(name="nt", environ=os.environ, path=os.path)
            with patch.dict(os.environ, {"MINDIE_KIMI_CONFIG": str(cfg)}), \
                 patch.object(with_runtime, "os", fake_os), \
                 patch("subprocess.run", return_value=done) as run_mock:
                with self.assertRaises(SystemExit) as ctx:
                    with_runtime.main([str(script), "--flag"])
            self.assertEqual(ctx.exception.code, 17)
            self.assertEqual(
                run_mock.call_args.kwargs.get("creationflags"),
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertIs(run_mock.call_args.kwargs.get("stdin"), sys.stdin)
            self.assertIs(run_mock.call_args.kwargs.get("stdout"), sys.stdout)
            self.assertIs(run_mock.call_args.kwargs.get("stderr"), sys.stderr)

            current = {"python": "C:/venv/Scripts/python.exe", "generation": raw}
            with patch.object(mindie_launch, "_state_dir", return_value=Path(raw)), \
                 patch.object(mindie_launch, "_current", return_value=current), \
                 patch.object(mindie_launch, "_config_path", return_value=cfg), \
                 patch.object(mindie_launch, "_child_env", return_value={"A": "1", "PYTHONPATH": "x"}), \
                 patch("paths.same_interpreter", return_value=False), \
                 patch.object(mindie_launch, "os", fake_os), \
                 patch("subprocess.run", return_value=subprocess.CompletedProcess([], 23)) as mcp_run:
                self.assertEqual(mindie_launch._mcp("remote"), 23)
                self.assertNotIn("PYTHONPATH", mcp_run.call_args.kwargs["env"])
                self.assertIs(mcp_run.call_args.kwargs.get("stdin"), sys.stdin)
                self.assertIs(mcp_run.call_args.kwargs.get("stdout"), sys.stdout)
                self.assertIs(mcp_run.call_args.kwargs.get("stderr"), sys.stderr)


if __name__ == "__main__":
    unittest.main()
