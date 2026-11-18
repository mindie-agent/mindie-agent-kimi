"""[Role] Session-scoped admission lease wrapper around mindie_knowledge.loop.activation.Admission.
[Invariants] Knowledge tools are enabled by default unless a session is explicitly deactivated; read-only checks never create admission.sqlite3.
[Timeouts & Locks] Delegates to shared SQLite WAL admission store (busy_timeout=5000ms).
[Side-Effect Policy] Mutates admission.sqlite3 only on activate(), ensure_default_lease(), or deactivate().
"""

from __future__ import annotations

import os
from pathlib import Path

from paths import admission_path


def gate(engine=None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(admission_path(engine))


def knowledge_lease(session: str, *, engine=None) -> dict | None:
    if not isinstance(session, str) or not session:
        return None
    try:
        path = admission_path(engine)
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    if not path.is_file():
        return None
    return gate(engine).knowledge_lease(session)


def is_deactivated(session: str, *, engine=None) -> bool:
    if not isinstance(session, str) or not session:
        return False
    try:
        path = admission_path(engine)
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return False
    if not path.is_file():
        return False
    return gate(engine).inspect(session).get("status") == "inactive"


def _resolve_default_root(session: str, project_root: str | Path | None = None) -> str:
    if isinstance(project_root, (str, Path)) and str(project_root) and os.path.isabs(str(project_root)):
        return str(Path(project_root).resolve())
    try:
        from identity import session_cwd

        return str(Path(session_cwd(session)).resolve())
    except TimeoutError:
        raise
    except (OSError, ValueError):
        pass
    default_lease = knowledge_lease(session)
    if default_lease and isinstance(default_lease.get("project_root"), str):
        return str(Path(default_lease["project_root"]).resolve())
    return str(Path.cwd().resolve())


def ensure_default_lease(
    session: str,
    *,
    project_root: str | Path | None = None,
    root_session: str | None = None,
    not_before: float | None = 0.0,
    engine=None,
) -> dict:
    if is_deactivated(session, engine=engine):
        raise ValueError("MindIE knowledge is deactivated for this session")
    existing = knowledge_lease(session, engine=engine)
    if existing is not None:
        return existing
    root = _resolve_default_root(session, project_root)
    adm = gate(engine)
    adm.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return adm.activate(
        session,
        project_root=root,
        root_session=root_session or session,
        not_before=not_before,
    )


def activate(session, *, project_root, root_session=None, not_before=None, engine=None):
    return gate(engine).activate(
        session,
        project_root=str(Path(project_root).resolve()),
        root_session=root_session,
        not_before=not_before,
    )


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)
