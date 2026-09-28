"""Community sharing via the shared core validator. Default off.

The settings file is the one designated authority: reads use
``consent.configured_community_path`` as-is (no implicit adoption or
fallback inside a read). Path convergence happens once at an explicit
install/upgrade/entry boundary (``consent.adopt_community_settings``).

Every mutation runs inside the ONE shared write boundary —
``CommunityWriteContext`` anchored at the profile's canonical community
path, even while the declared target is still a legacy file — so a user
disable and a migration/stamp can never lose each other (core API.md §9).
The current document is re-read inside the lock; no caller-supplied stale
snapshot. Every write carries the ``consent_config`` extension pointing at
the same profile consent authority, so the runtime gate checks the saved
choice at its own capture/model/write boundaries.
"""

from __future__ import annotations

from pathlib import Path

import consent
from consent import configured_community_path


def _settings_mod():
    from mindie_knowledge.loop import settings as settings_mod

    return settings_mod


def _write_context():
    """The profile's one community write boundary, anchored at the canonical
    path regardless of which file is the current authority."""
    return _settings_mod().CommunityWriteContext(str(consent.shared_community_path()))


def load(config=None):
    path = configured_community_path() if config is None else config
    return _settings_mod().load(path)


def public_status(config=None):
    return load(config).public_status()


def capture_allowed(lease, cwd, config=None) -> bool:
    settings = load(config)
    if not settings.allows_capture():
        return False
    if not lease or not isinstance(lease.get("project_root"), str):
        return False
    if not settings.in_scope(lease["project_root"]):
        return False
    if cwd and not settings.in_scope(cwd):
        return False
    return True


def write_enabled(
    *,
    repository,
    project_roots,
    branch="main",
    visibility="public",
    account=None,
    fork=None,
    config=None,
):
    if visibility != "public":
        raise ValueError("community sharing requires public visibility")
    with _write_context() as ctx:
        target = Path(config) if config is not None else configured_community_path()
        settings = ctx.write(
            target,
            enabled=True,
            repository=repository,
            project_roots=project_roots,
            branch=branch,
            visibility="public",
            account=account,
            fork=fork,
            consent_config=str(consent.consent_path()),
        )
    return settings.public_status()


def write_disabled(config=None):
    with _write_context() as ctx:
        target = Path(config) if config is not None else configured_community_path()
        # The merge base is the CURRENT document read inside the lock; a
        # missing file is a first configuration and a corrupt one is an
        # honest failure (bytes preserved), never a fabricated overwrite.
        current = ctx.read(target)
        raw = current.raw if current.schema_ok else {}
        settings = ctx.write(
            target,
            enabled=False,
            repository=raw.get("repository"),
            project_roots=list(raw.get("project_roots") or []),
            branch=raw.get("branch") or "main",
            consent_config=str(consent.consent_path()),
        )
    return settings.public_status()
