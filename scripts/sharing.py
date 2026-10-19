"""[Role] Community sharing configuration reader and writer backed by mindie_knowledge.loop.settings.
[Invariants] Reads from configured_community_path() without implicit fallback; all writes serialize through CommunityWriteContext at shared_community_path().
[Timeouts & Locks] Acquires CommunityWriteContext lock for all mutations and re-reads current document inside the lock.
[Side-Effect Policy] write_enabled() and write_disabled() atomically update the target community config JSON with consent_config attached.
"""

from __future__ import annotations

from pathlib import Path

import consent
from consent import configured_community_path


def _settings_mod():
    from mindie_knowledge.loop import settings as settings_mod

    return settings_mod


def _write_context():
    return _settings_mod().CommunityWriteContext(str(consent.shared_community_path()))


def load(config=None):
    return _settings_mod().load(configured_community_path() if config is None else config)


def public_status(config=None):
    return load(config).public_status()


def capture_allowed(lease, cwd, config=None) -> bool:
    settings = load(config)
    if not settings.allows_capture():
        return False
    if not lease or not isinstance(lease.get("project_root"), str):
        return False
    return settings.in_scope(lease["project_root"]) and (not cwd or settings.in_scope(cwd))


def write_enabled(
    *,
    repository,
    project_roots,
    branch="main",
    visibility="public",
    account=None,
    fork=None,
    config=None,
    extend_scope=False,
):
    if visibility != "public":
        raise ValueError("community sharing requires public visibility")
    with _write_context() as ctx:
        target = Path(config) if config is not None else configured_community_path()
        current = ctx.read(target)
        if account is None:
            account = current.raw.get("account")
        if (
            fork is None
            and current.repository == repository
            and current.branch == branch
            and current.raw.get("account") == account
        ):
            fork = current.raw.get("fork")
        roots = list(project_roots)
        if (
            extend_scope
            and current.enabled
            and current.repository == repository
            and current.branch == branch
            and current.raw.get("account") == account
            and current.raw.get("fork") == fork
        ):
            roots = list(dict.fromkeys([*roots, *map(str, current.project_roots)]))
        settings = ctx.write(
            target,
            enabled=True,
            repository=repository,
            project_roots=roots,
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
