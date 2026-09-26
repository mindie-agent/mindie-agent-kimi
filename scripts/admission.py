"""Thin wrapper: shared Admission only. No local lease or attempt SQL.

The lease is the internal per-task identity binding established by the
entry; it is never a consent prompt and never failure-paused.
"""

from __future__ import annotations

from pathlib import Path

from paths import admission_path


def gate(engine=None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(admission_path(engine))


def activate(session, *, project_root, root_session=None, engine=None):
    return gate(engine).activate(
        session,
        project_root=str(Path(project_root).resolve()),
        root_session=root_session,
    )


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)
