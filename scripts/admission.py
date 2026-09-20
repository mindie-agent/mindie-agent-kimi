"""Canonical Admission wrapper: SessionGate semantics, kimi namespace.

check(session, token=None) — active task, optional activation token.
resolve(token) — activation token to its valid lease.
claim(...) — unique attempt, returns bool (first True, any repeat False).
finish(session, activation_token, succeeded) — failure circuit on that lease.
"""

from __future__ import annotations

import hmac
import sqlite3
from pathlib import Path

from paths import admission_path


def gate(engine=None):
    from mindie_knowledge.loop.activation import Admission

    return Admission(admission_path(engine))


def activate(session, *, project_root, root_session=None, engine=None):
    return gate(engine).activate(
        session, project_root=str(Path(project_root).resolve()), root_session=root_session
    )


def deactivate(session, engine=None):
    return gate(engine).deactivate(session)


def check(session, token=None, engine=None):
    admission = gate(engine)
    if token is None:
        lease = admission.active_lease(session)
        if lease is None:
            raise ValueError("session is not manually activated")
        return lease
    return admission.check(session, token)


def resolve(token, engine=None):
    if not isinstance(token, str) or not token:
        return None
    admission = gate(engine)
    for lease in admission.leases():
        if hmac.compare_digest(str(lease.get("token") or ""), token):
            return lease
    return None


def _attempts_db(admission):
    path = Path(admission.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=1.0)
    db.execute(
        "CREATE TABLE IF NOT EXISTS adapter_attempts("
        "session TEXT NOT NULL, kind TEXT NOT NULL, identity TEXT NOT NULL, "
        "PRIMARY KEY(session, kind, identity))"
    )
    return db


def claim(session, kind, identity, token=None, engine=None) -> bool:
    lease = check(session, token, engine=engine)
    if token is not None and lease.get("token") != token:
        raise ValueError("activation token does not match this session")
    if not isinstance(kind, str) or not kind.strip() or not isinstance(identity, str) or not identity.strip():
        raise ValueError("kind and identity must be nonempty text")
    admission = gate(engine)
    db = _attempts_db(admission)
    try:
        with db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                "INSERT OR IGNORE INTO adapter_attempts VALUES(?,?,?)",
                (session, kind.strip(), identity.strip()),
            )
            return cursor.rowcount == 1
    finally:
        db.close()


def finish(session, activation_token, succeeded, engine=None) -> None:
    lease = resolve(activation_token, engine=engine)
    if lease is None or lease.get("session") != session:
        raise ValueError("unknown or inactive activation token")
    admission = gate(engine)
    db = sqlite3.connect(admission.path, timeout=1.0)
    try:
        with db:
            if succeeded:
                db.execute(
                    "UPDATE leases SET failures=0 WHERE session=? AND enabled=1 AND failures<3",
                    (session,),
                )
            else:
                db.execute(
                    "UPDATE leases SET failures=failures+1 WHERE session=? AND enabled=1",
                    (session,),
                )
    finally:
        db.close()
