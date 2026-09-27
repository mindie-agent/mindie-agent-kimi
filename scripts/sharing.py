"""Community sharing via the shared core validator. Default off.

The settings file is the one designated authority: reads use
``consent.configured_community_path`` as-is (no implicit adoption or
fallback inside a read). Path convergence happens once at an explicit
install/upgrade/entry boundary (``consent.adopt_community_settings``).
Every write carries the ``consent_config`` extension pointing at the same
profile consent authority, so the runtime gate checks the saved choice at
its own capture/model/write boundaries.
"""

from __future__ import annotations

from pathlib import Path

import consent
from consent import configured_community_path


def _settings_mod():
    from mindie_knowledge.loop import settings as settings_mod

    return settings_mod


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
    path = configured_community_path() if config is None else config
    settings = _settings_mod().write(
        path,
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
    path = configured_community_path() if config is None else config
    previous = {}
    try:
        raw = Path(path).read_text()
        import json

        previous = json.loads(raw)
    except (OSError, ValueError):
        previous = dict(schema="mindie-community-config/1", repository="local/unconfigured")
    repository = previous.get("repository") or "local/unconfigured"
    roots = previous.get("project_roots") or []
    settings = _settings_mod().write(
        path,
        enabled=False,
        repository=repository,
        project_roots=roots,
        branch=previous.get("branch", "main"),
        previous=previous,
        consent_config=str(consent.consent_path()),
    )
    return settings.public_status()
