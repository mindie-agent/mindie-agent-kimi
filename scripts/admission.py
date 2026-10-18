"""[Role] Thin per-task admission lease wrapper around mindie_knowledge.loop.activation.Admission.
[Invariants] Lease binds a Kimi session_id to a resolved project_root; never acts as a consent prompt or failure-pause gate.
[Timeouts & Locks] Delegates to shared SQLite WAL admission store (busy_timeout=5000ms).
[Side-Effect Policy] Mutates admission.sqlite3 only on explicit activate() or deactivate() calls.
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
