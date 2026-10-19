"""Install-level consent and community-settings path convergence for Kimi.

[Role]
Resolves the profile-shared `mindie-consent.json` and `mindie-community.json`
paths, records user consent choices, and converges generation-stranded files
onto the profile authority without one-way fault lockouts.

[Invariants]
- Authority files live alongside `profile_config_path()` (`mindie-consent.json`
  and `mindie-community.json`).
- Stranded generation files are adopted by latest timestamp when the profile
  authority is missing; an existing shared authority is never overwritten by a
  legacy generation file.

[Timeouts & Locks]
- Uses `_UpdateLock` (`5.0s` wait) for consent writes and `CommunityWriteContext`
  for community-settings convergence.

[Side-Effect Policy]
- Writes `mindie-consent.json`, `mindie-community.json`, `kimi.json`, and
  `kimi.engine.json` atomically via `os.replace` only during explicit record or
  adoption operations (`load()` and status queries never mutate files).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

try:
    from mindie_knowledge import consent_store
except ImportError as exc:
    raise RuntimeError(
        "MindIE shared consent runtime is unavailable; run scripts/setup.py "
        "and use the configured runtime Python before changing consent."
    ) from exc

from paths import (
    community_config_path,
    config_path,
    first_use_path,
    profile_config_path,
    state_dir,
)


SCHEMA = consent_store.SCHEMA
CHOICES = consent_store.CHOICES
REPORTING = consent_store.REPORTING


class ConsentDamaged(RuntimeError):
    def __init__(self, state, error):
        super().__init__(error)
        self.state = state


def consent_path() -> Path:
    return profile_config_path().parent / "mindie-consent.json"


def shared_community_path() -> Path:
    return profile_config_path().parent / "mindie-community.json"


def _stranded_candidates(filename: str) -> list[Path]:
    profile = profile_config_path().parent / filename
    seen = []
    try:
        root = state_dir() / "update" / "generations"
    except (OSError, ValueError, FileNotFoundError):
        root = None
    if root is not None and root.is_dir():
        for path in sorted(root.glob("*/config/" + filename)):
            if path != profile and path.is_file():
                seen.append(path)
    active = config_path().parent / filename
    if active != profile and active.is_file() and active not in seen:
        seen.append(active)
    return seen


def _read_json(path: Path):
    try:
        raw = consent_store._read_bytes(path)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    try:
        data = json.loads(raw)
    except ValueError:
        return "corrupt", None
    return ("ok", data) if isinstance(data, dict) else ("corrupt", None)


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _store(path: Path, data: dict) -> None:
    _atomic_write_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _shared_file_lock(path: Path):
    return consent_store._UpdateLock(path.with_name(path.name + ".lock"))


def _marker_path():
    try:
        return first_use_path()
    except (OSError, ValueError, FileNotFoundError):
        return None


def load() -> dict:
    return consent_store.read(consent_path())


def record_choice(choice: str) -> str:
    if choice not in CHOICES:
        raise ValueError("choice must be contribute, read-only, later or disabled")
    path = consent_path()
    try:
        consent_store.record_choice(path, choice)
    except consent_store.ConsentError as exc:
        raise ConsentDamaged(exc.state, str(exc)) from None
    return choice




def marker_exists() -> bool:
    marker = _marker_path()
    return bool(marker is not None and marker.exists())


def configured_community_path(config=None) -> Path:
    try:
        return community_config_path(config)
    except FileNotFoundError:
        return shared_community_path()


def install_traces() -> bool:
    marker = _marker_path()
    if consent_path().exists() or (marker is not None and marker.exists()) or shared_community_path().exists():
        return True
    try:
        legacy = community_config_path()
    except (OSError, ValueError, FileNotFoundError):
        return False
    return bool(legacy is not None and legacy.exists())


def _consent_rank(path: Path, view: dict) -> tuple[float, float]:
    ts = max(float(view.get("choice_at") or 0.0), float(view.get("reporting_at") or 0.0))
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (ts, mtime)


def adopt_stranded_consent() -> dict:
    profile = consent_path()
    result = dict(action="already", notes=[])
    with _shared_file_lock(profile):
        if consent_store.read(profile)["state"] == "ok":
            return result
        valid, damaged = [], []
        for path in _stranded_candidates("mindie-consent.json"):
            view = consent_store.read(path)
            if view["state"] == "ok" and (view.get("choice") is not None or view.get("reporting") is not None):
                valid.append((path, view))
            elif view["state"] != "missing":
                damaged.append((path, view["state"]))
        if not valid:
            if damaged:
                result["notes"].append(f"ignored damaged stranded consent: {damaged[0][0]}")
            return result
        path, view = max(valid, key=lambda item: _consent_rank(item[0], item[1]))
        try:
            _atomic_write_bytes(profile, consent_store._read_bytes(path))
        except OSError as exc:
            result.update(action="error")
            result["notes"].append(f"stranded consent adoption failed: {type(exc).__name__}")
            return result
        result.update(
            action="adopted",
            choice=view.get("choice"),
            reporting=view.get("reporting"),
            sources=[str(p) for p, _v in valid],
        )
        if len(valid) > 1:
            result["notes"].append(f"converged multiple stranded consent files to latest: {path}")
        if damaged:
            result["notes"].append(f"damaged stranded consent left as evidence: {damaged[0][0]}")
    return result


def _divergence(shared: Path, legacy: Path):
    s_state, s_data = _read_json(shared)
    l_state, l_data = _read_json(legacy)
    if s_state != "ok" or l_state != "ok":
        return {"shared": s_state} if s_state != "ok" else {"legacy": l_state}
    differ = {k: "differs" for k in ("enabled", "repository", "branch", "project_roots") if s_data.get(k) != l_data.get(k)}
    return differ or None


def _stamp_consent_key(ctx, shared: Path, result: dict) -> None:
    if not shared.exists():
        return
    current = ctx.read(shared)
    if current.error is not None or not current.schema_ok or current.raw.get("consent_config") == str(consent_path()):
        return
    try:
        ctx.update_extensions(shared, consent_config=str(consent_path()))
    except ValueError as exc:
        result["notes"].append(f"consent_config stamp failed: {str(exc)[:120]}")


def _repoint_config_paths(shared: Path, adapter_path: Path, adapter: dict) -> None:
    from paths import engine_config_path

    if adapter.get("community_config") != str(shared):
        adapter["community_config"] = str(shared)
        _store(adapter_path, adapter)
    try:
        engine_path = engine_config_path(adapter)
        state, engine = _read_json(engine_path)
        if state == "ok" and engine.get("community_config") != str(shared):
            engine["community_config"] = str(shared)
            _store(engine_path, engine)
    except (OSError, ValueError, FileNotFoundError):
        pass


def _select_latest_community(candidates: list[Path]) -> Path | None:
    best_path, best_rank = None, (-1, False)
    for path in candidates:
        state, data = _read_json(path)
        if state != "ok":
            continue
        # enabled_at describes an activation, not the last user decision:
        # disabling clears it. Equal-time conflicts cannot renew consent.
        rank = (path.stat().st_mtime_ns, data.get("enabled") is False)
        if rank >= best_rank:
            best_rank = rank
            best_path = path
    return best_path or (candidates[-1] if candidates else None)


def adopt_community_settings() -> dict:
    from mindie_knowledge.loop.settings import CommunityWriteContext
    from paths import load_adapter_config

    shared = shared_community_path()
    result = dict(path=str(shared), action="already", conflict=None, notes=[])
    with CommunityWriteContext(str(shared)) as ctx:
        try:
            adapter_path = config_path()
            adapter = load_adapter_config()
        except (OSError, ValueError, FileNotFoundError) as exc:
            result.update(action="error")
            result["notes"].append(f"adapter config unavailable: {type(exc).__name__}")
            return result
        legacy_value = adapter.get("community_config")
        legacy = None
        already = legacy_value == str(shared)
        if isinstance(legacy_value, str) and os.path.isabs(legacy_value):
            candidate = Path(legacy_value)
            if candidate != shared:
                legacy = candidate
        elif legacy_value is not None:
            result["notes"].append("adapter community_config repointed to the shared authority")
        if legacy is None:
            if stranded := _stranded_candidates("mindie-community.json"):
                legacy = _select_latest_community(stranded)
                if len(stranded) > 1:
                    result["notes"].append("converged multiple stranded community files to latest: " + str(legacy))
        if legacy is not None and legacy.is_file():
            if not shared.exists():
                try:
                    _atomic_write_bytes(shared, consent_store._read_bytes(legacy))
                except OSError as exc:
                    result.update(action="error")
                    result["notes"].append(f"legacy adoption failed: {type(exc).__name__}")
                    return result
                result["action"] = "adopted"
            else:
                if _divergence(shared, legacy):
                    result["notes"].append(
                        "legacy community file disagrees with the shared authority; "
                        "the shared file was kept and the legacy file left as evidence"
                    )
                result["action"] = "converged"
        elif shared.exists():
            current = ctx.read(shared)
            if current.error is not None or not current.schema_ok:
                result["notes"].append(f"shared community settings are {current.state}")
            if not already:
                result["action"] = "converged"
        else:
            result["action"] = "already" if already else "repointed"
        _stamp_consent_key(ctx, shared, result)
        _repoint_config_paths(shared, adapter_path, adapter)
    return result
