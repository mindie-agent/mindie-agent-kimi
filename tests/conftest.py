"""Autouse isolation and pre-0.1.5 `knowledge_binding` test shim for MindIE Agent Kimi tests."""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import site
import sys
import tempfile
import time
import types
from typing import Any
import venv

import pytest

ENV_VARS = (
    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CONFIG_HOME",
    "XDG_DATA_HOME", "XDG_STATE_HOME", "MINDIE_CONFIG_HOME", "MINDIE_DATA_HOME",
    "MINDIE_KIMI_CONFIG", "MINDIE_AGENT_CONFIG", "MINDIE_DIAGNOSTICS_ROOT",
    "MINDIE_DIAGNOSTICS_CONFIG", "KIMI_CODE_HOME", "REMOTE_DEV_AUTH_DIR",
    "UV_CACHE_DIR", "TMPDIR",
)

_HOST_ADAPTER_RELPATHS: dict[str, tuple[str, ...]] = {
    "codex": ("plugins/mindie-agent/scripts/codex_transcript.py", "scripts/codex_transcript.py"),
    "cc": ("scripts/transcript.py",),
    "kimi": ("scripts/kimi_transcript.py",),
    "dsh": ("scripts/dsh_transcript.py",),
    "agy": ("plugins/mindie-agent/scripts/agy_transcript.py", "scripts/agy_transcript.py"),
    "wb": ("plugins/mindie-agent/scripts/wb_transcript.py", "scripts/wb_transcript.py"),
    "oc": ("plugins/mindie-agent/scripts/opencode_transcript.py", "scripts/opencode_transcript.py"),
}


def _resolve_transcript_adapter(host: str, plugin_root: Path | None, state_dir: Path) -> Path:
    if plugin_root is not None:
        for rel in _HOST_ADAPTER_RELPATHS.get(host, ("scripts/kimi_transcript.py",)):
            cand = Path(plugin_root) / rel
            if cand.is_file():
                return cand.resolve()
    noop = state_dir / "noop_transcript_adapter.py"
    if not noop.is_file():
        noop.parent.mkdir(parents=True, exist_ok=True)
        noop.write_text(
            "from dataclasses import dataclass\n"
            "@dataclass(frozen=True)\n"
            "class FileIdentity:\n"
            "    path: str; dev: int = 0; ino: int = 0; size: int = 0; mtime_ns: int = 0\n"
            "def identify(path): return None\n"
            "def read_material(path, start, **_kw): return {'status': 'unchanged', 'start': start, 'end': start, 'digest': '', 'text': '', 'records': 0}\n",
            encoding="utf-8",
        )
    return noop.resolve()


def _resolve_redactor(data_dir: Path) -> str:
    if explicit := os.environ.get("MINDIE_REDACTOR_EXECUTABLE"):
        cand = Path(explicit.strip()).expanduser()
        if cand.is_absolute() and cand.is_file():
            return str(cand.resolve())
    if found := shutil.which("gitleaks"):
        return str(Path(found).resolve())
    binary = "gitleaks.exe" if os.name == "nt" else "gitleaks"
    return str((data_dir / "cache" / "gitleaks" / "8.30.1" / binary).resolve())


def _shim_ensure_knowledge_binding(
    profile: Any,
    *,
    plugin_root: Path | None = None,
    python_executable: str | Path | None = None,
    install_redactor: bool = False,
    retire_previous: bool = False,
    include_default_feed: bool = False,
    already_locked: bool = False,
) -> dict[str, Any]:
    from mindie_harness.atomic import atomic_write_json, atomic_write_text, read_json_safe
    from mindie_harness.locks import FileLock, LockTimeoutError
    from mindie_knowledge.loop import domain_catalog
    from mindie_knowledge.loop.handoff import prepare_schema

    _ = install_redactor
    eff_root = Path(plugin_root).resolve() if plugin_root is not None else Path(profile.plugin_root).resolve()
    eff_py = Path(python_executable) if python_executable is not None else Path(sys.executable)

    lock = None
    if not already_locked:
        lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
        try:
            lock.acquire()
        except LockTimeoutError:
            return {"status": "deferred", "reason": "lock_timeout", "reportable": False}

    try:
        prev_raw = read_json_safe(profile.config_path)
        prev_cfg = dict(prev_raw) if isinstance(prev_raw, dict) else {}
        prev_eng_raw = prev_cfg.get("engine_config")
        prev_eng = read_json_safe(prev_eng_raw) if isinstance(prev_eng_raw, str) and prev_eng_raw.strip() else None
        if not isinstance(prev_eng, dict):
            leg = read_json_safe(profile.config_path.with_name(f"{profile.config_path.stem}.engine.json"))
            prev_eng = dict(leg) if isinstance(leg, dict) else {}

        env_root = os.environ.get("MINDIE_KNOWLEDGE_ROOT")
        prev_root = prev_eng.get("root")
        if isinstance(env_root, str) and env_root.strip():
            kb_root = Path(env_root.strip()).expanduser().resolve(strict=False)
        elif isinstance(prev_root, str) and prev_root.strip():
            kb_root = Path(prev_root).expanduser().resolve(strict=False)
        else:
            kb_root = (profile.data_dir / "knowledge").resolve(strict=False)

        transcript_path = _resolve_transcript_adapter(profile.host, eff_root, profile.state_dir)
        redactor_path = _resolve_redactor(profile.data_dir)
        comm_path = (profile.config_path.parent / "mindie-community.json").resolve(strict=False)
        consent_path = profile.consent_path.resolve(strict=False)
        admission_path = profile.admission_db.resolve(strict=False)
        default_domain = "vllm-ascend"
        default_engine: dict[str, Any] = {
            "root": str(kb_root),
            "domain": default_domain,
            "domain_catalog": domain_catalog.SCHEMA,
            "admission_path": str(admission_path),
            "community_config": str(comm_path),
            "capture_mode": "public-transcript",
            "transcript_adapter": str(transcript_path),
            "redactor_executable": redactor_path,
            "product_validation": {
                "schema": "mindie-candidate-check/1",
                "status": "validated",
                "python": str(eff_py),
            },
        }
        if prev_eng.get("feeds"):
            default_engine["feeds"] = list(prev_eng["feeds"])
        elif include_default_feed:
            default_engine["feeds"] = [domain_catalog.get_domain_config(default_domain).to_feed()]

        engines_dir = (profile.config_path.parent / "engines" / profile.host).resolve(strict=False)
        authority_dir = (profile.config_path.parent / "authorities" / profile.host).resolve(strict=False)
        default_text = json.dumps(default_engine, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        default_path = engines_dir / hashlib.sha256(default_text.encode("utf-8")).hexdigest()[:20] / f"{default_domain}.engine.json"
        if not default_path.is_file() or default_path.read_text(encoding="utf-8") != default_text:
            atomic_write_text(default_path, default_text)
        prepare_schema(str(default_path))

        existing_routes = {
            k: v for k, v in (prev_cfg.get("domain_configs") or {}).items()
            if isinstance(k, str) and k != default_domain and isinstance(v, str) and Path(v).is_file()
        }
        prepared = domain_catalog.prepare(
            {"engine_config": str(default_path), "domain_configs": existing_routes},
            default_engine,
            config_directory=str(engines_dir),
            authority_directory=str(authority_dir),
            consent_config=str(consent_path),
            public_feeds=True,
        )
        routes = dict(prepared["domain_configs"])
        routes[default_domain] = str(default_path)
        if retire_previous and (prev_cfg.get("engine_config") or prev_cfg.get("domain_configs")):
            try:
                domain_catalog.retire_changed(
                    prev_cfg, {"engine_config": str(default_path), "domain_configs": routes}, include_default=True,
                )
            except TimeoutError:
                raise
            except Exception:
                pass

        binding_fields = {
            "host": profile.host,
            "engine_config": str(default_path),
            "domain_configs": routes,
            "community_config": str(comm_path),
            "consent_config": str(consent_path),
            "admission_path": str(admission_path),
        }
        if not already_locked:
            updated = dict(prev_cfg)
            updated.setdefault("python", str(eff_py))
            updated.setdefault("source_root", str(eff_root))
            updated.update(binding_fields)
            updated["updated_at"] = time.time()
            atomic_write_json(profile.config_path, updated)

        return {
            "status": "configured",
            "engine_config": str(default_path),
            "domain_configs": routes,
            "domains": sorted(routes),
            "transcript_adapter": str(transcript_path),
            "standalone_adapter": transcript_path.name == "noop_transcript_adapter.py",
            "redactor_executable": redactor_path,
            "binding_fields": binding_fields,
            "reportable": False,
        }
    finally:
        if lock is not None:
            lock.release()


def _authorized_for_stop(profile: Any, cwd: str) -> bool:
    from mindie_harness.atomic import read_json_safe

    consent = read_json_safe(profile.consent_path)
    consent_doc = consent if isinstance(consent, Mapping) else {}
    comm_path = profile.config_path.parent / "mindie-community.json"
    comm = read_json_safe(comm_path)
    choice = consent_doc.get("choice")
    if choice not in {"contribute", "local-only"} and not (choice is None and consent_doc.get("enabled") is True):
        return False
    if comm_path.is_file():
        if not isinstance(comm, Mapping) or comm.get("enabled") is not True:
            return False
        roots = comm.get("project_roots")
    else:
        roots = consent_doc.get("project_roots")
    if not isinstance(roots, list) or not roots or not os.path.isabs(cwd):
        return False
    cand = os.path.normcase(str(Path(cwd).resolve()))
    for raw in roots:
        if not isinstance(raw, str) or not os.path.isabs(raw):
            return False
        norm_root = os.path.normcase(str(Path(raw).resolve()))
        if os.path.splitdrive(cand)[0] != os.path.splitdrive(norm_root)[0]:
            continue
        try:
            if os.path.commonpath([cand, norm_root]) == norm_root:
                return True
        except ValueError:
            continue
    return False


def _shim_run_stop_capture(
    profile: Any,
    event: Mapping[str, Any],
    *,
    plugin_root: Path | None = None,
    timeout_sec: float = 6.0,
) -> dict[str, Any]:
    from mindie_harness.atomic import read_json_safe

    cwd = event.get("cwd") or event.get("workspace") or event.get("project_root")
    session_id = event.get("session_id")
    if not isinstance(cwd, str) or not isinstance(session_id, str) or not session_id.strip():
        return {"stage": "rejected", "reason": "missing-identity"}
    if not _authorized_for_stop(profile, cwd):
        return {"stage": "inert", "reason": "sharing-disabled"}

    cfg = read_json_safe(profile.config_path)
    engine_path_raw = cfg.get("engine_config") if isinstance(cfg, Mapping) else None
    if not isinstance(engine_path_raw, str) or not Path(engine_path_raw).is_file():
        bound = _shim_ensure_knowledge_binding(profile, plugin_root=plugin_root, python_executable=sys.executable)
        engine_path_raw = bound["engine_config"]
    engine_doc = read_json_safe(Path(engine_path_raw))
    adm_raw = engine_doc.get("admission_path") if isinstance(engine_doc, Mapping) else None
    if not isinstance(adm_raw, str) or not adm_raw.strip():
        return {"stage": "unavailable", "reason": "missing-admission"}

    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import capture_hook

    admission = Admission(Path(adm_raw))
    inspected = admission.inspect(session_id)
    if inspected.get("status") == "inactive":
        return {"stage": "inert", "reason": "revoked"}
    if inspected.get("status") == "active" and inspected.get("token"):
        lease = inspected
    else:
        lease = admission.activate(session_id, project_root=str(Path(cwd).resolve()), not_before=0.0)
    payload = dict(event)
    if payload.get("identity_kind") == "notification":
        payload.pop("last_assistant_message", None)
    payload["mindie_activation"] = str(lease.get("token") or "")
    return capture_hook(str(engine_path_raw), payload, timeout=timeout_sec)


def install_knowledge_binding_shim_in_process() -> bool:
    """Install in-process `mindie_harness.runtime.knowledge_binding` only if `< 0.1.5`."""
    try:
        if importlib.util.find_spec("mindie_harness.runtime.knowledge_binding") is not None:
            return False
    except (ImportError, ValueError):
        pass
    import mindie_harness.runtime as runtime_pkg

    mod = types.ModuleType("mindie_harness.runtime.knowledge_binding")
    mod.ensure_knowledge_binding = _shim_ensure_knowledge_binding  # type: ignore[attr-defined]
    mod.run_stop_capture = _shim_run_stop_capture  # type: ignore[attr-defined]
    sys.modules["mindie_harness.runtime.knowledge_binding"] = mod
    setattr(runtime_pkg, "knowledge_binding", mod)
    return True


def create_subprocess_shim_venv(target_dir: Path) -> Path:
    """Return `sys.executable` on `>= 0.1.5`, or a fast `.pth`-shimmed temp venv on `< 0.1.5`."""
    if not install_knowledge_binding_shim_in_process():
        return Path(sys.executable)
    venv_dir = target_dir / "kb-shim-venv"
    py_rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    shim_py = venv_dir / py_rel
    if shim_py.is_file():
        return shim_py
    venv.EnvBuilder(with_pip=False).create(venv_dir)
    if os.name == "nt":
        sp_dir = venv_dir / "Lib" / "site-packages"
    else:
        ver = f"python{sys.version_info.major}.{sys.version_info.minor}"
        sp_dir = venv_dir / "lib" / ver / "site-packages"
    sp_dir.mkdir(parents=True, exist_ok=True)
    tests_dir = str(Path(__file__).resolve().parent)
    parent_sps = [p for p in site.getsitepackages() if Path(p).is_dir()]
    adds = "; ".join(f"site.addsitedir({p!r})" for p in parent_sps)
    lines = [
        tests_dir,
        f"import site; {adds}; import conftest; conftest.install_knowledge_binding_shim_in_process()",
    ]
    (sp_dir / "zz_mindie_kb_shim.pth").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return shim_py


install_knowledge_binding_shim_in_process()


@pytest.fixture(scope="session")
def _session_shim_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create_subprocess_shim_venv(tmp_path_factory.mktemp("mindie-kb-shim"))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _session_shim_python: Path) -> Path:
    """Point every host/config/data/diagnostics/temp location at one temp root."""
    root = tmp_path / "home"
    root.mkdir()
    temp = tmp_path / "tmp"
    temp.mkdir()
    values = {
        "HOME": root, "USERPROFILE": root,
        "APPDATA": root / "AppData/Roaming", "LOCALAPPDATA": root / "AppData/Local",
        "XDG_CONFIG_HOME": root / ".config", "XDG_DATA_HOME": root / ".local/share",
        "XDG_STATE_HOME": root / ".local/state",
        "MINDIE_CONFIG_HOME": root / ".config/mindie-agent",
        "MINDIE_DATA_HOME": root / ".local/share/mindie-agent",
        "MINDIE_KIMI_CONFIG": root / ".config/mindie-agent/kimi.json",
        "MINDIE_AGENT_CONFIG": root / ".config/mindie-agent/kimi.json",
        "MINDIE_DIAGNOSTICS_ROOT": root / ".local/share/mindie-agent/diagnostics",
        "MINDIE_DIAGNOSTICS_CONFIG": root / ".config/mindie-agent/diagnostics.json",
        "KIMI_CODE_HOME": root / ".kimi-code",
        "REMOTE_DEV_AUTH_DIR": root / ".remote-dev",
        "UV_CACHE_DIR": temp / "uv-cache", "TMPDIR": temp,
    }
    for name in ENV_VARS:
        monkeypatch.setenv(name, str(values.get(name, temp)))
    monkeypatch.setenv("MINDIE_KIMI_SKIP_SCHTASKS", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    monkeypatch.setattr(sys, "executable", str(_session_shim_python))
    for name in ("MINDIE_KIMI_BIN", "MINDIE_REDACTOR_EXECUTABLE", "MINDIE_KNOWLEDGE_ROOT"):
        monkeypatch.delenv(name, raising=False)
    return root


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture()
def plugin_env(isolated_home: Path) -> dict[str, str]:
    """Environment for CLI subprocesses under the isolated home (keeps PYTHONPATH)."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    return env
