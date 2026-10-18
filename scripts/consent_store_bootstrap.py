"""Stdlib bootstrap fallback for install-level consent persistence."""

from __future__ import annotations

import errno, json, os, stat, tempfile, time
from pathlib import Path

SCHEMA = "mindie-consent/1"
CHOICES = ("contribute", "read-only", "later", "disabled")
REPORTING = ("enabled", "disabled", "later")
STATES = ("ok", "missing", "unreadable", "corrupt")
MAX_BYTES = 64 * 1024
LOCK_WAIT_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.05
__all__ = ["SCHEMA", "CHOICES", "REPORTING", "STATES", "MAX_BYTES",
           "ConsentError", "read", "record_choice", "record_reporting"]

class ConsentError(Exception):
    def __init__(self, message, *, state):
        super().__init__(message)
        if state not in ("corrupt", "unreadable", "locked"):
            raise ValueError("ConsentError state must be corrupt, unreadable or locked")
        self.state = state

def _lock_op(fd, lock: bool):
    if os.name == "nt":
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK if lock else msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, (fcntl.LOCK_EX | fcntl.LOCK_NB) if lock else fcntl.LOCK_UN)

class _UpdateLock:
    def __init__(self, path, wait=LOCK_WAIT_SECONDS):
        self.path, self.wait, self._fd = Path(path), wait, None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            deadline = time.monotonic() + self.wait
            while True:
                try:
                    _lock_op(fd, True)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise ConsentError("consent update lock is held by another process", state="locked") from None
                    time.sleep(LOCK_POLL_SECONDS)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(self, *_exc):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                _lock_op(fd, False)
            except OSError:
                pass
            os.close(fd)

def _open_for_read(path):
    return open(path, "rb")

def _read_raw(path):
    try:
        with _open_for_read(path) as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise OSError(errno.EINVAL, "authority is not a regular file")
            raw = stream.read(MAX_BYTES + 1)
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "unreadable", None
    if len(raw) > MAX_BYTES:
        return "corrupt", None
    try:
        data = json.loads(raw)
    except ValueError:
        return "corrupt", None
    return ("ok", data) if isinstance(data, dict) else ("corrupt", None)

def _validate(data):
    if data.get("schema") != SCHEMA:
        return "unsupported consent schema"
    if data.get("choice") is not None and data.get("choice") not in CHOICES:
        return "unknown consent choice"
    if data.get("reporting") is not None and data.get("reporting") not in REPORTING:
        return "unknown reporting choice"
    return None

def read(path):
    path = Path(path)
    result = dict(state=None, choice=None, reporting=None, choice_at=None, reporting_at=None, path=str(path), error=None)
    state, data = _read_raw(path)
    result["state"] = state
    if state in ("unreadable", "corrupt", "missing"):
        if state != "missing":
            result["error"] = "consent file is unreadable" if state == "unreadable" else "consent file is damaged"
        return result
    if (error := _validate(data)) is not None:
        result.update(state="corrupt", error=error)
        return result
    result["choice"], result["reporting"] = data.get("choice"), data.get("reporting")
    for key in ("choice_at", "reporting_at"):
        result[key] = data.get(key) if type(data.get(key)) in (int, float) else None
    return result

def _atomic_replace(source, target):
    os.replace(source, target)

def _write_document(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write((json.dumps(data, indent=2) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        _atomic_replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def _record(path, mutate):
    path = Path(path)
    with _UpdateLock(path.with_name(path.name + ".lock")):
        state, data = _read_raw(path)
        if state == "unreadable":
            raise ConsentError("consent file is unreadable", state="unreadable")
        doc = {k: v for k, v in (data or {}).items() if k not in ("choice", "reporting") or (k == "choice" and v in CHOICES) or (k == "reporting" and v in REPORTING)} if isinstance(data, dict) else {}
        mutate(doc)
        doc["schema"] = SCHEMA
        if state != "ok" or doc != data:
            _write_document(path, doc)
    return read(path)

def record_choice(path, choice):
    if choice not in CHOICES:
        raise ValueError("choice must be contribute, read-only, later or disabled")
    return _record(path, lambda doc: doc.update(schema=SCHEMA, choice=choice, choice_at=time.time()) if doc.get("choice") != choice else None)

def record_reporting(path, value):
    if value not in REPORTING:
        raise ValueError("reporting must be enabled, disabled or later")
    return _record(path, lambda doc: doc.update(schema=SCHEMA, reporting=value, reporting_at=time.time()))
