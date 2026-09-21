"""Community sharing via the shared core validator. Default off."""

from __future__ import annotations

from pathlib import Path

from paths import community_config_path, load_engine_config, load_adapter_config


def _settings_mod():
    from mindie_knowledge.loop import settings as settings_mod

    return settings_mod


def load(config=None):
    path = community_config_path(config)
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
    path = community_config_path(config)
    settings = _settings_mod().write(
        path,
        enabled=True,
        repository=repository,
        project_roots=project_roots,
        branch=branch,
        visibility="public",
        account=account,
        fork=fork,
    )
    return settings.public_status()


def write_disabled(config=None):
    path = community_config_path(config)
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
    )
    return settings.public_status()
