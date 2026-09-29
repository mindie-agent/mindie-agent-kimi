"""One attempt with an absolute deadline and bounded output; own the child tree.

POSIX reads pipes with a selector and kills the owned process group. Windows
cannot select() process pipes, so it uses two daemon reader threads and an
owned Windows Job. The child starts suspended until Job assignment completes;
cleanup still owns descendants after their leader exits.

When this process is already the leader of an owned process group
(MINDIE_MAINTENANCE_GROUP=1 set by the outer core), POSIX spawns stay in that
group instead of a new session: the outer core owns whole-group terminal
cleanup, so this module only kills the direct child, never the group.

Final cleanup closes a standalone owned tree; managed descendants are closed
by the outer core when the organizer returns.
Stdin is a temporary file, never a blocking pipe write: a child that does not
read cannot hang the parent past the deadline.

An optional cancel Event is checked before spawn, while draining/waiting
(including after the child has closed its pipes), and before returning a
result. Stderr bytes count toward the output bound and are then discarded.
Updater transport commands may attach a static failure category; raw
stderr, argv and URLs are not retained. This call does not retry.
"""

from __future__ import annotations

import atexit
import os
import re
import subprocess
import tempfile
import threading
import time

POSIX = os.name == "posix"

if POSIX:
    import selectors
    import signal

MAX_OUTPUT = 256 * 1024
_CHUNK = 8192


class CommandCancelled(RuntimeError):
    """The caller cancelled this attempt; owned children are reaped."""


class CommandTimedOut(RuntimeError):
    """The command did not finish before the absolute deadline."""

    def __init__(self, message, *args, category=None, retry_after=None):
        super().__init__(message, *args)
        self.category = category
        self.retry_after = retry_after


class OutputLimitExceeded(ValueError, RuntimeError):
    """Captured output grew past the configured byte bound."""


def _own_windows_tree(process):
    """Keep descendant ownership after the process group leader exits.

    A taskkill PID traversal cannot find a tree whose root has already exited.
    A Windows Job retains that ownership, including inherited pipe holders.
    This launcher is stdlib-only because it also runs before runtime setup.
    """
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                               ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    job = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # KILL_ON_JOB_CLOSE
    if (not job or not kernel.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            or not kernel.AssignProcessToJobObject(job, int(process._handle))):
        error = ctypes.get_last_error()
        if job:
            kernel.CloseHandle(job)
        raise ctypes.WinError(error)
    process._mindie_windows_job = (kernel, job)


def _spawn(command, stdin, env, cwd):
    inherited = (
        POSIX
        and os.environ.get("MINDIE_MAINTENANCE_GROUP") == "1"
        and os.getpgrp() == os.getpid()
    )
    kwargs = dict(
        args=list(command),
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        cwd=cwd,
    )
    if POSIX:
        kwargs["start_new_session"] = not inherited
    else:
        # Assign ownership before the child can create descendants. Attaching
        # a Job to an already-running process races fast child startup.
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
    process = subprocess.Popen(**kwargs)
    process._mindie_inherited_group = inherited
    if not POSIX:
        try:
            _own_windows_tree(process)
            _resume_windows_process(process)
        except OSError:
            # Do not continue an operation without the promised ownership.
            _kill_tree(process)
            process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            raise
    return process


def _resume_windows_process(process):
    """Resume the suspended primary thread through documented Win32 APIs.

    Popen closes the thread handle returned by CreateProcess, so reopen this
    process's sole suspended thread from a Toolhelp snapshot. Its process
    handle stays owned throughout; no shell or system-wide process kill.
    """
    import ctypes
    from ctypes import wintypes

    class ThreadEntry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ThreadID", wintypes.DWORD),
                    ("th32OwnerProcessID", wintypes.DWORD),
                    ("tpBasePri", wintypes.LONG), ("tpDeltaPri", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Thread32First.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.Thread32Next.argtypes = [wintypes.HANDLE, ctypes.POINTER(ThreadEntry)]
    kernel.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenThread.restype = wintypes.HANDLE
    kernel.ResumeThread.argtypes = [wintypes.HANDLE]
    kernel.ResumeThread.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(0x4, 0)  # TH32CS_SNAPTHREAD
    if snapshot == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ThreadEntry()
        entry.dwSize = ctypes.sizeof(entry)
        more = kernel.Thread32First(snapshot, ctypes.byref(entry))
        while more:
            if entry.th32OwnerProcessID == process.pid:
                thread = kernel.OpenThread(0x2, False, entry.th32ThreadID)  # SUSPEND_RESUME
                if not thread:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    previous = kernel.ResumeThread(thread)
                    if previous == 0xFFFFFFFF:
                        raise ctypes.WinError(ctypes.get_last_error())
                finally:
                    kernel.CloseHandle(thread)
                if previous == 1:
                    return
                if previous > 1:
                    raise OSError("owned primary thread has an unexpected suspend count")
                # An injected, already-running thread is not the primary
                # thread we created suspended. Continue to the owned one.
            more = kernel.Thread32Next(snapshot, ctypes.byref(entry))
        raise OSError("owned suspended process has no primary thread")
    finally:
        kernel.CloseHandle(snapshot)


def _kill_tree(process, pgid=None):
    if POSIX:
        if getattr(process, "_mindie_inherited_group", False):
            # Killing the shared group here would kill this organizer before
            # it can report its result. Core owns the entire group on exit.
            if process.poll() is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            return
        try:
            os.killpg(pgid if pgid is not None else process.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        return
    owned = getattr(process, "_mindie_windows_job", None)
    if owned is not None:
        process._mindie_windows_job = None
        kernel, job = owned
        try:
            kernel.TerminateJobObject(job, 1)
        finally:
            kernel.CloseHandle(job)
        return
    # Fallback is used only when initial Job assignment failed.
    if process.poll() is None:
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        try:
            subprocess.run(
                [os.path.join(system_root, "System32", "taskkill.exe"),
                 "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()


class _Cap:
    def __init__(self, max_output):
        self.max_output = max_output
        self.size = 0

    def add(self, chunk):
        self.size += len(chunk)
        if self.size > self.max_output:
            raise OutputLimitExceeded("output exceeds the bound")


def _cancelled(cancel):
    return cancel is not None and cancel.is_set()


_RETRY_AFTER = re.compile(r"retry-after\s*[:=]\s*(\d{1,6})\b", re.I)
_HTTP_STATUS = re.compile(
    r"(?:http\s*/\s*1\.[01]\s+|status(?:\s+code)?\s*[:=]\s*|"
    r"error\s*[:=]\s*|returned error:\s*|http error\s+|http\s+)(\d{3})\b",
    re.I,
)
_CERT_PHRASES = (
    "certificate verify failed",
    "sslcertverificationerror",
    "certificate has expired",
    "self-signed certificate",
    "self signed certificate",
    "unable to get local issuer certificate",
    "certificate_verify_failed",
    "ssl: certificate",
    "ssl certificate problem",
    "unknown ca",
    "curl: (60)",
)
_RESOLVER_PHRASES = (
    "resolutionimpossible",
    "conflicting dependencies",
    "package versions have conflicting",
    "the conflict is caused by",
    "resolver conflict",
)
_HASH_PHRASES = (
    "these packages do not match the hashes",
    "does not match the hashes",
    "hash mismatch",
)
_GENERIC_CONTENT_PHRASES = (
    "no matching distribution",
    "could not find a version that satisfies",
)
_AUTH_PHRASES = (
    "authentication failed",
    "could not read username",
    "terminal prompts disabled",
    "permission denied (publickey)",
    "invalid credentials",
    "http basic: access denied",
    "support for password authentication was removed",
    "authentication required",
    "invalid username or password",
)
_RATE_PHRASES = (
    "rate limit",
    "too many requests",
    "secondary rate limit",
)
_HOOK_PHRASES = (
    "hook trust",
    "requires native trust",
    "changed hooks require",
)
_CONNECT_PHRASES = (
    "could not resolve host",
    "temporary failure in name resolution",
    "name or service not known",
    "nodename nor servname",
    "temporary failure resolving",
    "network is unreachable",
    "connection timed out",
    "connection reset by peer",
    "connection refused",
    "connection aborted",
    "failed to connect",
    "operation timed out",
    "read operation timed out",
    "timed out",
    "curl: (6)",
    "curl: (7)",
    "curl: (28)",
    "curl: (56)",
    "newconnectionerror",
    "remote end closed connection",
    "unexpected eof",
    "connection broken",
    "bad gateway",
    "service unavailable",
    "gateway time-out",
    "gateway timeout",
    "error sending request",
    "dns error",
    "recv failure",
    "send failure",
    "max retries exceeded",
    "proxy connect aborted",
)


def _has_phrase(text, phrases):
    return any(phrase in text for phrase in phrases)


def classify_transport_text(text):
    """Return (category, retry_after_seconds) or None.

    `text` is command output examined only in memory. The result is a static
    category and an optional Retry-After. It never includes the output.
    Exit status alone is not a category: 128 and a bare 403 are not enough.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    sample = text[:65536]
    lower = sample.lower()
    retry_after = None
    match = _RETRY_AFTER.search(sample)
    if match:
        retry_after = _finite_delay(match.group(1))
    codes = {int(item) for item in _HTTP_STATUS.findall(sample)}
    if _has_phrase(lower, _CERT_PHRASES):
        return ("certificate", None)
    if _has_phrase(lower, _HASH_PHRASES):
        return ("bad_content", None)
    if _has_phrase(lower, _RESOLVER_PHRASES):
        return ("resolver", None)
    if 429 in codes or _has_phrase(lower, _RATE_PHRASES):
        return ("rate_limited", retry_after)
    if 401 in codes or _has_phrase(lower, _AUTH_PHRASES):
        return ("authentication", None)
    if 403 in codes or (
        ("access denied" in lower and "http basic" not in lower)
        or "write access to repository not granted" in lower
        or ("forbidden" in lower and "403" in lower)
        or ("permission denied" in lower and "publickey" not in lower)
    ):
        return ("permission", None)
    if _has_phrase(lower, _HOOK_PHRASES):
        return ("hook_trust", None)
    # A demonstrated transport failure wins over pip's generic final summary.
    if (codes & {500, 502, 503, 504}) or _has_phrase(lower, _CONNECT_PHRASES):
        return ("temporary_network", retry_after)
    if _has_phrase(lower, _GENERIC_CONTENT_PHRASES):
        return ("bad_content", None)
    return None


def _finite_delay(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number == float("inf") or number == float("-inf") or number < 0:
        return None
    return number


_STREAM_EDGE = 4096


def _stream_edges(buf):
    """Head and tail of one stream. The raw bytes are not retained."""
    if not buf:
        return ""
    if isinstance(buf, str):
        text = buf
    else:
        text = bytes(buf).decode("utf-8", "replace")
    if len(text) <= _STREAM_EDGE * 2:
        return text
    return text[:_STREAM_EDGE] + "\n" + text[-_STREAM_EDGE:]


def _joined_output(stdout, stderr):
    parts = []
    for buf in (stdout, stderr):
        piece = _stream_edges(buf)
        if piece:
            parts.append(piece)
    return "\n".join(parts)


def _attach_transport(exc, stdout, stderr, *, timed_out):
    kind = classify_transport_text(_joined_output(stdout, stderr))
    if kind is not None:
        category, retry_after = kind
        exc.category = category
        if retry_after is not None:
            exc.retry_after = retry_after
        return
    if timed_out:
        exc.category = "temporary_network"


def _run_posix(process, timeout, max_output, pgid, check, cancel, transport=False):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    deadline = time.monotonic() + timeout
    output = bytearray()
    errors = bytearray() if transport else None
    cap = _Cap(max_output)

    def timeout_error():
        err = TimeoutError("deadline exceeded")
        if transport:
            _attach_transport(err, output, errors, timed_out=True)
        return err

    def failed_error():
        err = RuntimeError(f"command failed ({process.returncode})")
        if transport:
            _attach_transport(err, output, errors, timed_out=False)
        return err

    try:
        while True:
            if _cancelled(cancel):
                raise CommandCancelled("command cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise timeout_error()
            alive = process.poll() is None
            if not alive and not selector.get_map():
                break
            if selector.get_map():
                for key, _ in selector.select(min(0.05, remaining)):
                    chunk = os.read(key.fileobj.fileno(), _CHUNK)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    cap.add(chunk)
                    if key.data == "out":
                        output.extend(chunk)
                    elif errors is not None:
                        errors.extend(chunk)
                continue
            # Pipes closed while the child is still running.
            try:
                process.wait(timeout=min(0.05, remaining))
            except subprocess.TimeoutExpired:
                continue
            break
        if _cancelled(cancel):
            raise CommandCancelled("command cancelled")
        if process.poll() is None:
            try:
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                err = timeout_error()
                if transport:
                    raise err from None
                raise err
        if _cancelled(cancel):
            raise CommandCancelled("command cancelled")
        if check and process.returncode:
            raise failed_error()
        return output.decode("utf-8", "replace")
    finally:
        _kill_tree(process, pgid)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _kill_tree(process, pgid)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _run_windows(process, timeout, max_output, pgid, check, cancel, transport=False):
    # Windows reader threads drain the pipes; the Job owns all pipe holders.
    deadline = time.monotonic() + timeout
    output = bytearray()
    errors = bytearray() if transport else None
    cap = _Cap(max_output)
    lock = threading.Lock()
    failure = []

    def reader(stream, dest):
        try:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    return
                with lock:
                    cap.add(chunk)
                    if dest is not None:
                        dest.extend(chunk)
        except (ValueError, OutputLimitExceeded) as exc:
            failure.append(exc)

    threads = [
        threading.Thread(target=reader, args=(process.stdout, output), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, errors), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads) or process.poll() is None:
            if _cancelled(cancel):
                raise CommandCancelled("command cancelled")
            if time.monotonic() >= deadline:
                err = TimeoutError("deadline exceeded")
                if transport:
                    _attach_transport(err, output, errors, timed_out=True)
                raise err
            if failure:
                raise failure[0]
            if process.poll() is not None and not any(
                thread.is_alive() for thread in threads
            ):
                break
            time.sleep(0.02)
        if failure:
            raise failure[0]
        if process.poll() is None:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if _cancelled(cancel):
            raise CommandCancelled("command cancelled")
        if check and process.returncode:
            err = RuntimeError(f"command failed ({process.returncode})")
            if transport:
                _attach_transport(err, output, errors, timed_out=False)
            raise err
        return bytes(output).decode("utf-8", "replace")
    finally:
        _kill_tree(process, pgid)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        for thread in threads:
            thread.join(timeout=1)
        process.stdout.close()
        process.stderr.close()


def run(
    argv,
    stdin="",
    *,
    timeout,
    env=None,
    cwd=None,
    max_output=MAX_OUTPUT,
    check=True,
    cancel=None,
    transport=False,
):
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("command must be a nonempty argv list")
    if timeout is None or timeout <= 0:
        raise ValueError("timeout must be positive")
    if _cancelled(cancel):
        raise CommandCancelled("command cancelled")
    data = stdin.encode() if isinstance(stdin, str) else (stdin or b"")
    with tempfile.TemporaryFile() as stream:
        if data:
            stream.write(data)
            stream.seek(0)
        process = _spawn(argv, stream, env, cwd)
        pgid = None
        if POSIX:
            try:
                pgid = os.getpgid(process.pid)
            except OSError:
                pgid = process.pid

        def cleanup():
            _kill_tree(process, pgid)

        atexit.register(cleanup)
        try:
            if POSIX:
                result = _run_posix(
                    process, timeout, max_output, pgid, check, cancel, transport
                )
            else:
                result = _run_windows(
                    process, timeout, max_output, pgid, check, cancel, transport
                )
            if _cancelled(cancel):
                raise CommandCancelled("command cancelled")
            return result
        except CommandCancelled:
            raise
        except OutputLimitExceeded:
            raise
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            message = f"command timed out after {timeout}s"
            category = getattr(exc, "category", None)
            retry_after = getattr(exc, "retry_after", None)
            if transport and category is None:
                category = "temporary_network"
            if category:
                raise CommandTimedOut(
                    message, category=category, retry_after=retry_after
                ) from None
            raise CommandTimedOut(message) from exc
        finally:
            try:
                atexit.unregister(cleanup)
            except Exception:
                pass
