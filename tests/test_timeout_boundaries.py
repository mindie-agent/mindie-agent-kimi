"""Timeouts retain their identity across adapter I/O and helper boundaries."""

import importlib.util
import json
from pathlib import Path
import sys
import time

import pytest

from support import SCRIPTS, make_config, write_session

sys.path.insert(0, str(SCRIPTS))

import identity
import mindie_launch
import organizer
import service_handoff
import transcript
from mindie_knowledge.loop.cli import connection_path
from mindie_knowledge.loop.locks import StartLock, lock_held


def test_native_profile_timeout_does_not_become_configuration_unavailable(tmp_path, monkeypatch):
    fault = TimeoutError("profile read exceeded its deadline")
    monkeypatch.setattr(organizer, "auth_home", lambda: tmp_path)

    def read(*_args, **_kwargs):
        raise fault

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(TimeoutError) as raised:
        organizer._profile()
    assert raised.value is fault


def test_fork_lineage_timeout_is_not_rewritten_as_unavailable_evidence(tmp_path, monkeypatch):
    _, wire = write_session(tmp_path, "child", [], state={"forkedFrom": "parent", "createdAt": 1000})
    original = Path.read_text
    fault = TimeoutError("lineage read exceeded its deadline")

    def read(path, *args, **kwargs):
        if path.name == "state.json":
            raise fault
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(TimeoutError) as raised:
        transcript.read_material(str(wire), 0, session_id="child")
    assert raised.value is fault


def test_skill_path_timeout_is_not_rewritten_as_invalid_activation(tmp_path, monkeypatch):
    skill = tmp_path / "skill.md"
    original = Path.resolve
    fault = TimeoutError("skill path resolution timed out")

    def resolve(path, *args, **kwargs):
        if path == skill:
            raise fault
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    with pytest.raises(TimeoutError) as raised:
        identity._current_skill_activation(
            {"skillName": "mindie-agent", "skillPath": str(skill)}, "init",
        )
    assert raised.value is fault


def test_operation_lock_timeout_is_not_retried_as_contention(tmp_path, monkeypatch):
    fault = TimeoutError("lock operation timed out")
    attempts = []

    def acquire(descriptor):
        attempts.append(descriptor)
        raise fault

    monkeypatch.setattr(mindie_launch, "_try_lock_shared", acquire)
    with pytest.raises(TimeoutError) as raised:
        mindie_launch._shared_lock(tmp_path, time.monotonic() + 2)
    assert raised.value is fault
    assert len(attempts) == 1


def test_service_stop_timeout_does_not_claim_absent_or_release_live_owner(tmp_path, monkeypatch):
    adapter = make_config(tmp_path)
    engine = json.loads(adapter.read_text())["engine_config"]
    config = json.loads(Path(engine).read_text())
    consumer = connection_path(config).with_name("consumer.lock")
    monkeypatch.setattr(service_handoff, "connect",
                        lambda *_: {"url": "http://127.0.0.1:1", "token": "test"})
    fault = TimeoutError("stop acknowledgement deadline")

    def rpc(*_args, **_kwargs):
        raise fault

    monkeypatch.setattr(service_handoff, "rpc", rpc)
    with StartLock(consumer):
        with pytest.raises(TimeoutError) as raised:
            service_handoff.stop(engine)
        assert raised.value is fault
        assert lock_held(consumer) is True


def test_runtime_probe_preserves_timeout_instead_of_installation_rejection(monkeypatch):
    spec = importlib.util.spec_from_file_location("kimi_setup_timeout", SCRIPTS / "setup.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    fault = TimeoutError("runtime probe deadline")

    def run(*_args, **_kwargs):
        raise fault

    monkeypatch.setattr(setup, "run", run)
    with pytest.raises(TimeoutError) as raised:
        setup.probe_runtime(sys.executable)
    assert raised.value is fault
