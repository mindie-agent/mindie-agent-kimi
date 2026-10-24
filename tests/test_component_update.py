"""Component-only switching preserves the native plugin identity and old binding."""
import json
from pathlib import Path
import sys
import time
from unittest.mock import Mock

import pytest
from support import SCRIPTS, make_config

sys.path.insert(0, str(SCRIPTS))
import genstate
import updater


def generations(tmp_path):
    path = make_config(tmp_path / 'profile')
    adapter = json.loads(path.read_text())
    configs = []
    for name in ('old', 'candidate'):
        generation = tmp_path / name
        generation.mkdir()
        config = generation / 'adapter.json'
        config.write_text(json.dumps(dict(adapter, domain_configs={})))
        configs.append(dict(generation=str(generation), python=sys.executable,
                            adapter_config=str(config), sha='a' * 40))
    genstate.write_current(configs[0], adapter)
    return adapter, configs


def test_component_switch_preserves_plugin_sha_without_native_install(tmp_path):
    adapter, (old, candidate) = generations(tmp_path)
    install = Mock()
    updater.switch(adapter, old, Path(candidate['generation']), Path(candidate['python']),
                   Path(candidate['adapter_config']), old['sha'], time.monotonic() + 120,
                   lambda *_: {'service': 'absent'}, install, 1, update_native=False)
    assert genstate.read_current(adapter) == candidate
    install.assert_not_called()


def test_busy_work_prevents_component_publication_without_native_mutation(tmp_path):
    adapter, (old, candidate) = generations(tmp_path)
    install = Mock()
    def busy(*_):
        raise updater.Deferred('active RPC')
    with pytest.raises(updater.Deferred, match='active RPC'):
        updater.switch(adapter, old, Path(candidate['generation']), Path(candidate['python']),
                       Path(candidate['adapter_config']), old['sha'], time.monotonic() + 120,
                       busy, install, 1, update_native=False)
    assert genstate.read_current(adapter) == old
    install.assert_not_called()
