"""Thin wrapper: shared Admission only. No local lease or attempt SQL."""

from __future__ import annotations

from pathlib import Path

from paths import admission_path


def gate(engine=None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(admission_path(engine))


def activate(session, *, project_root, root_session=None, engine=None):
    lease = gate(engine).activate(
        session,
        project_root=str(Path(project_root).resolve()),
        root_session=root_session,
    )
    if not lease.get("enabled") or lease.get("failures", 0) >= 3:
        raise ValueError(
            "activation is paused; /mindie-agent:deactivate then init to recover"
        )
    return lease


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)
