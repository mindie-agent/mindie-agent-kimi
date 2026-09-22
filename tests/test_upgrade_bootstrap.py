"""Old two-file launcher packaging must bootstrap only its own generation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SHA = 'a' * 40


class UpgradeBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.update = self.root / 'state/update'
        self.generation = self.update / 'generations' / SHA
        self.scripts = self.generation / 'scripts'
        self.launch = self.update / 'launch' / SHA
        self.scripts.mkdir(parents=True)
        self.launch.mkdir(parents=True)
        for name in ('diagnostic_support.py', 'diagnostic_fallback.py'):
            shutil.copy2(ROOT / 'scripts' / name, self.scripts / name)
        for name in ('mindie_launch.py', 'bounded.py'):
            shutil.copy2(ROOT / 'scripts' / name, self.launch / name)
        package = self.generation / 'host-package'
        package.mkdir()
        # Real installed manifests can exceed the small diagnostic stamp limit.
        (package / 'kimi.plugin.json').write_text(json.dumps({'version': '0.1.0+mindie.' + SHA[:12], 'padding': 'x' * 3000}))
        (self.generation / '.mindie-generation-complete').write_text(SHA + '\n')
        (self.update / 'current.json').write_text(json.dumps({'sha': 'b' * 40, 'generation': 'never-use-current-for-module'}))
        self.decoy = self.root / 'decoy'
        self.decoy.mkdir()
        for name in ('diagnostic_support.py', 'diagnostic_fallback.py'):
            (self.decoy / name).write_text("raise AssertionError('PYTHONPATH module must not load')\n")
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('MINDIE_')}
        self.env.update(PYTHONPATH=str(self.decoy), PYTHONDONTWRITEBYTECODE='1',
                        MINDIE_DIAGNOSTICS_CONFIG=str(self.root / 'reporting.json'),
                        MINDIE_DIAGNOSTICS_ROOT=str(self.root / 'logs'))
        self.prelude = (
            'import importlib.util,json,sys,time; from pathlib import Path; '
            'p=Path(sys.argv[1]); sys.path.insert(0,str(p.parent)); '
            'spec=importlib.util.spec_from_file_location("front",p); '
            'm=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); '
        )

    def probe(self, code):
        return subprocess.run([sys.executable, '-c', self.prelude + code, str(self.launch / 'mindie_launch.py')],
                              env=self.env, capture_output=True, text=True, timeout=5)

    def metadata(self):
        result = self.probe('print(json.dumps(m.diagnostic_support.build_metadata()))')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_old_packed_stop_and_same_generation_ignore_pythonpath_and_current(self):
        result = subprocess.run([sys.executable, str(self.launch / 'mindie_launch.py'), '--config', str(self.root / 'missing.json'), 'hook', 'stop'],
                                env=self.env, input='{}', capture_output=True, text=True, timeout=5)
        self.assertEqual((result.returncode, json.loads(result.stdout)), (0, {}))
        self.assertFalse((self.root / 'logs').exists())
        self.assertEqual(self.metadata(), {'version': '0.1.0+mindie.' + SHA[:12], 'revision': SHA})
        self.assertEqual(sorted(p.name for p in self.launch.iterdir()), ['bounded.py', 'mindie_launch.py'])
        self.assertFalse((self.scripts / 'diagnostic-build.json').exists())

    def test_missing_or_mismatched_completion_never_guesses_revision(self):
        marker = self.generation / '.mindie-generation-complete'
        marker.unlink()
        self.assertEqual(self.metadata(), {'version': '0.1.0+mindie.' + SHA[:12]})
        marker.write_text('b' * 40 + '\n')
        self.assertNotIn('revision', self.metadata())

    def test_invalid_explicit_stamp_is_authoritative(self):
        stamp = self.scripts / 'diagnostic-build.json'
        stamp.write_text('invalid')
        self.assertEqual(self.metadata(), {})
        stamp.unlink()
        stamp.symlink_to(self.generation / '.mindie-generation-complete')
        self.assertEqual(self.metadata(), {})

    def test_adjacent_valid_stamp_takes_precedence(self):
        for name in ('diagnostic_support.py', 'diagnostic_fallback.py'):
            shutil.copy2(self.scripts / name, self.launch / name)
        (self.launch / 'diagnostic-build.json').write_text(json.dumps({'version': '0.1.0+actual', 'revision': 'c' * 40}))
        self.assertEqual(self.metadata(), {'version': '0.1.0+actual', 'revision': 'c' * 40})

    def test_missing_own_support_cannot_borrow_another_generation(self):
        (self.scripts / 'diagnostic_support.py').unlink()
        result = self.probe('print("unexpected")')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ModuleNotFoundError', result.stderr)
        self.assertNotIn('PYTHONPATH module must not load', result.stderr)

    def test_front_fault_is_attributed_to_own_build_not_selected_current(self):
        result = self.probe('m._dispatch_failure({"sha":"' + 'b' * 40 + '"},"helper_protocol","protocol_mismatch",ValueError("private text"),time.monotonic()); print("ok")')
        self.assertEqual(result.returncode, 0, result.stderr)
        records = [json.loads(line) for p in (self.root / 'logs').rglob('*.jsonl') for line in p.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['package_revision'], SHA)
        self.assertEqual(records[0]['package_version'], '0.1.0+mindie.' + SHA[:12])
        self.assertNotIn('private text', json.dumps(records))
