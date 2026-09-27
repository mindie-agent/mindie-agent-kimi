"""Install-level one-time choices: the adapter's path/evidence layer.

One small JSON document (``mindie-consent/1``) per installation profile,
stored beside the adapter configuration so every adapter sharing this
profile reads the same saved choice. An independently isolated profile has
its own file and never inherits another profile's choice.

All storage semantics (side-effect-free read, serialized field updates,
one-time boundary migration) are delegated to the ONE shared
implementation: the selected runtime's ``mindie_knowledge.consent_store``
in normal operation, or the byte-identical bootstrap copy
``scripts/consent_store_bootstrap.py`` (source: knowledge repo
``mindie_knowledge/consent_store.py`` @ 9beb317a2e6f0898cd8507f710e4ddb0f93a69f4,
SHA-256 b6f309777d4ac56a73871c5aa7bb260ab3b7a6b3226ef33dc61eb86b2d200b2d)
when the runtime cannot be loaded before first setup. Do not hand-edit the
copy and do not fork the semantics here.

This module keeps only adapter concerns: the profile paths, the legacy
evidence locations (first-use marker, community settings), the explicit
repair flow, and the community-settings path convergence at explicit
install/upgrade/entry boundaries.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from paths import community_config_path, config_path, first_use_path


def _store_mod():
    """The ONE shared consent store: the runtime module when the selected
    runtime provides it, else the byte-identical bootstrap copy."""
    try:
        from mindie_knowledge import consent_store as mod
    except ImportError:
        import consent_store_bootstrap as mod
    return mod


SCHEMA = _store_mod().SCHEMA
CHOICES = _store_mod().CHOICES
REPORTING = _store_mod().REPORTING


class ConsentDamaged(RuntimeError):
    """A saved consent document is corrupt or unreadable: field updates are
    refused so a damaged file is never silently emptied. Only the explicit,
    entry-verified repair flow rewrites it."""

    def __init__(self, state, error):
        super().__init__(error)
        self.state = state


def consent_path() -> Path:
    """The profile-shared consent document (sibling of the adapter config)."""
    return config_path().parent / "mindie-consent.json"


def shared_community_path() -> Path:
    """The profile-shared community settings path."""
    return config_path().parent / "mindie-community.json"


def _read_json(path: Path):
    """(state, data): ok / missing / unreadable / corrupt. Never raises."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    if len(raw) > 64 * 1024:
        return "corrupt", None
    try:
        data = json.loads(raw)
    except ValueError:
        return "corrupt", None
    if not isinstance(data, dict):
        return "corrupt", None
    return "ok", data


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    """Unique temp file + atomic replace (adapter/engine/community config
    plumbing only — consent documents go through the shared store)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
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
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _store(path: Path, data: dict) -> None:
    _atomic_write_bytes(path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))


def _shared_file_lock(path: Path):
    """The canonical cross-process lock of the shared consent store, reused
    for adapter-owned non-community files (slash activation dedupe) — no
    third lock protocol. Community settings writes use the core's
    CommunityWriteContext instead (see adopt_community_settings)."""
    return _store_mod()._UpdateLock(path.with_name(path.name + ".lock"))


def _marker_path():
    try:
        return first_use_path()
    except (OSError, ValueError, FileNotFoundError):
        return None


def load() -> dict:
    """Read the persistent choice through the shared store. Side-effect
    free: legacy state is never imported here (that happens at an explicit
    boundary; see ``adopt_legacy_choice``).

    Returns ``state`` of ``ok``/``missing``/``unreadable``/``corrupt`` plus
    the saved ``choice`` and ``reporting`` values when valid. An invalid
    saved choice value is corrupt, not absent.
    """
    return _store_mod().read(consent_path())


def record_choice(choice: str) -> str:
    """Explicit field update via the shared store (serialized merge; a
    damaged file is refused, never silently emptied)."""
    if choice not in CHOICES:
        raise ValueError("choice must be contribute, read-only, later or disabled")
    try:
        _store_mod().record_choice(consent_path(), choice)
    except _store_mod().ConsentError as exc:
        raise ConsentDamaged(exc.state, str(exc)) from None
    return choice


def record_reporting(value: str) -> str:
    """Explicit field update via the shared store; the contribution choice
    and untouched metadata survive. A damaged document is refused — repair
    is the user's explicit action (move the file aside and choose again),
    surfaced by the entry as a fault state, never an automatic rewrite."""
    if value not in REPORTING:
        raise ValueError("reporting must be enabled, disabled or later")
    try:
        _store_mod().record_reporting(consent_path(), value)
    except _store_mod().ConsentError as exc:
        raise ConsentDamaged(exc.state, str(exc)) from None
    return value


def marker_exists() -> bool:
    """A legacy first-use marker exists (any state): prior setup evidence."""
    marker = _marker_path()
    return bool(marker is not None and marker.exists())


def configured_community_path(config=None) -> Path:
    """The designated community settings authority. Pure: no adoption, no
    rewrite, no fallback to another candidate file. An unreadable authority
    stays an honest fault for its callers."""
    try:
        return community_config_path(config)
    except FileNotFoundError:
        # Unconfigured installation: the profile-shared default location.
        return shared_community_path()


def legacy_evidence(config=None) -> dict:
    """Pure read of legacy choice evidence: the first-use marker and the
    designated community settings file. Each value is a valid choice,
    ``"contribute"`` (settings explicitly enabled), ``"abstain"`` (valid
    file, no evidence either way — an installer default-off is not a
    choice), ``"corrupt"`` (damaged/unreadable) or None (absent)."""
    out = dict(marker=None, community=None, marker_path=None, community_path=None)
    marker = _marker_path()
    if marker is not None:
        out["marker_path"] = str(marker)
        state, data = _read_json(marker)
        if state == "ok":
            choice = data.get("choice")
            out["marker"] = choice if choice in CHOICES else "corrupt"
        elif state in {"corrupt", "unreadable"}:
            out["marker"] = "corrupt"
    try:
        community = configured_community_path(config)
    except (OSError, ValueError):
        community = None
    if community is not None:
        out["community_path"] = str(community)
        state, data = _read_json(community)
        if state == "ok":
            out["community"] = "contribute" if data.get("enabled") is True else "abstain"
        elif state in {"corrupt", "unreadable"}:
            out["community"] = "corrupt"
    return out


def adopt_legacy_choice() -> dict:
    """One-time consent migration at an explicit install/upgrade/entry
    boundary, through the shared store's ``migrate``. An existing consent
    document (any state) always wins. When it has no saved choice,
    consistent legacy evidence is imported exactly once; conflicting
    evidence is a diagnosable result, never a guessed choice.
    """
    result = dict(action="unchosen", choice=None, conflict=None, sources=[], notes=[])
    evidence = legacy_evidence()
    candidates = []
    if evidence["marker"] == "corrupt":
        result["notes"].append("first-use marker is damaged; not evidence")
    elif evidence["marker"]:
        candidates.append(dict(choice=evidence["marker"], reporting=None,
                               source="kimi-first-use-marker"))
    if evidence["community"] == "corrupt":
        result["notes"].append("community settings are damaged; not evidence")
    elif evidence["community"] == "contribute":
        candidates.append(dict(choice="contribute", reporting=None,
                               source="community-settings"))
    migrated = _store_mod().migrate(consent_path(), candidates)
    status = migrated["status"]
    if status == "kept":
        result.update(action="already", choice=migrated["choice"])
    elif status == "migrated":
        result.update(action="imported", choice=migrated["choice"],
                      sources=list(migrated["sources"]))
    elif status == "conflict":
        result.update(action="conflict", conflict=migrated["detail"])
    elif status == "error":
        result.update(action="fault")
        result["notes"].append(migrated["error"])
    else:  # absent
        result["action"] = "unchosen"
    return result


def install_traces() -> bool:
    """Any evidence this installation was set up before — consent (any
    state), community settings (shared or legacy, any state) or a legacy
    marker. A cold install has none; everything else is an existing
    installation and never re-runs first-time onboarding."""
    marker = _marker_path()
    if consent_path().exists() or (marker is not None and marker.exists()):
        return True
    if shared_community_path().exists():
        return True
    try:
        legacy = community_config_path()
    except (OSError, ValueError, FileNotFoundError):
        return False
    return bool(legacy is not None and legacy.exists())


def _divergence(shared: Path, legacy: Path):
    """Fields where a legacy community file disagrees with the shared
    authority (None when they converge). Read-only comparison."""
    s_state, s_data = _read_json(shared)
    l_state, l_data = _read_json(legacy)
    if s_state != "ok" or l_state != "ok":
        if s_state != "ok":
            return {"shared": s_state}
        return {"legacy": l_state}
    differ = {}
    for key in ("enabled", "repository", "branch", "project_roots"):
        if s_data.get(key) != l_data.get(key):
            differ[key] = "differs"
    return differ or None


def _stamp_consent_key(ctx, shared: Path, result: dict) -> None:
    """Wire the consent_config extension (absolute path of the same profile
    consent authority) onto a valid shared document via the held write
    context — a stamp never touches generation/enabled_at, so accepted work
    is never revoked. A damaged document keeps its honest state."""
    if not shared.exists():
        return
    current = ctx.read(shared)
    if current.error is not None or not current.schema_ok:
        return
    if current.raw.get("consent_config") == str(consent_path()):
        return
    try:
        ctx.update_extensions(shared, consent_config=str(consent_path()))
    except ValueError as exc:
        result["notes"].append(f"consent_config stamp failed: {str(exc)[:120]}")


def _repoint_config_paths(shared: Path, adapter_path: Path, adapter: dict) -> None:
    """Point adapter and engine config community_config keys at the shared
    authority. Data-only mutation at an explicit boundary, inside the held
    write context."""
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


def adopt_community_settings() -> dict:
    """One-time community-settings path convergence at an explicit
    install/upgrade/entry boundary, inside the ONE shared write context
    anchored at the canonical profile path (core API.md §9) — even while
    the declared authority is still the legacy file, so a concurrent user
    disable and this adoption serialize against each other and the current
    document is re-read inside the lock before any write.

    Afterwards the adapter config, engine config and workers all point at
    the profile-shared authority. A legacy adapter-specific file is adopted
    once (atomic copy; the legacy file is kept as evidence). When the shared
    file already exists it stays the authority: conflicting legacy values
    are diagnosed, never merged into a wider public scope and never
    silently adopted. A damaged shared file stays an honest fault.
    """
    from mindie_knowledge.loop.settings import CommunityWriteContext
    from paths import load_adapter_config

    shared = shared_community_path()
    result = dict(path=str(shared), action="already", conflict=None, notes=[])
    with CommunityWriteContext(str(shared)) as ctx:
        # Resolve and reload the CURRENT adapter config inside the lock: a
        # pre-lock snapshot must never be written back — another holder of
        # this boundary may have repointed or added fields concurrently.
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
            result["notes"].append(
                "adapter community_config was not an absolute path; repointed to the shared authority"
            )
        if legacy is not None and legacy.is_file():
            if not shared.exists():
                try:
                    _atomic_write_bytes(shared, legacy.read_bytes())
                except OSError as exc:
                    result.update(action="error")
                    result["notes"].append(f"legacy adoption failed: {type(exc).__name__}")
                    return result
                result["action"] = "adopted"
            else:
                conflict = _divergence(shared, legacy)
                if conflict:
                    result.update(action="conflict", conflict=conflict)
                    result["notes"].append(
                        "legacy community file disagrees with the shared authority; "
                        "the shared file was kept and the legacy file left as evidence"
                    )
                else:
                    result["action"] = "converged"
        elif shared.exists():
            current = ctx.read(shared)
            if current.error is not None or not current.schema_ok:
                result.update(action="fault")
                result["notes"].append(f"shared community settings are {current.state}")
            elif not already:
                result["action"] = "converged"
        else:
            result["action"] = "already" if already else "repointed"
        _stamp_consent_key(ctx, shared, result)
        _repoint_config_paths(shared, adapter_path, adapter)
    return result
