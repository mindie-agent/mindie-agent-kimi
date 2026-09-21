"""One attempt with an absolute deadline and bounded output; own the child tree.

POSIX reads pipes with a selector and kills the owned process group. Windows
cannot select() process pipes, so it uses two daemon reader threads and kills
the tree with taskkill /T. The Windows path uses only standard primitives but
has not been verified on real hardware yet.

When this process is already the leader of an owned process group
(MINDIE_MAINTENANCE_GROUP=1 set by the outer core), POSIX spawns stay in that
group instead of a new session: the outer core owns whole-group terminal
cleanup, so this module only kills the direct child, never the group.

Final cleanup closes a standalone owned tree; managed descendants are closed
by the outer core when the organizer returns.
Stdin is a temporary file, never a blocking pipe write: a child that does not
read cannot hang the parent past the deadline.
"""

from __future__ import annotations

import atexit
import os
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


class CommandTimedOut(RuntimeError):
    """The command did not finish before the absolute deadline."""


class OutputLimitExceeded(ValueError, RuntimeError):
    """Captured output grew past the configured byte bound."""


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
        # Windows (unverified on real hardware).
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    process = subprocess.Popen(**kwargs)
    process._mindie_inherited_group = inherited
    return process


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
    # Windows (unverified on real hardware): /T covers owned descendants.
    if process.poll() is None:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            capture_output=True,
            timeout=5,
        )


class _Cap:
    def __init__(self, max_output):
        self.max_output = max_output
        self.size = 0

    def add(self, chunk):
        self.size += len(chunk)
        if self.size > self.max_output:
            raise OutputLimitExceeded("output exceeds the bound")


def _run_posix(process, timeout, max_output, pgid, check):
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    deadline = time.monotonic() + timeout
    output = bytearray()
    errors = bytearray()
    cap = _Cap(max_output)
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("deadline exceeded")
            for key, _ in selector.select(min(0.05, remaining)):
                chunk = os.read(key.fileobj.fileno(), _CHUNK)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                cap.add(chunk)
                if key.data == "out":
                    output.extend(chunk)
                else:
                    errors.extend(chunk)
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError("deadline exceeded") from exc
        if check and process.returncode:
            err = bytes(errors[:400]).decode("utf-8", "replace")
            raise RuntimeError(f"command failed ({process.returncode}): {err}")
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


def _run_windows(process, timeout, max_output, pgid, check):
    # Windows (unverified on real hardware): reader threads replace selectors.
    deadline = time.monotonic() + timeout
    output = bytearray()
    errors = bytearray()
    cap = _Cap(max_output)
    lock = threading.Lock()
    failure = []

    def reader(stream, keep):
        try:
            while True:
                chunk = stream.read(_CHUNK)
                if not chunk:
                    return
                with lock:
                    cap.add(chunk)
                    if keep:
                        output.extend(chunk)
                    else:
                        errors.extend(chunk)
        except ValueError as exc:
            failure.append(exc)

    threads = [
        threading.Thread(target=reader, args=(process.stdout, True), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, False), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        while any(thread.is_alive() for thread in threads):
            if time.monotonic() >= deadline:
                raise TimeoutError("deadline exceeded")
            if failure:
                raise failure[0]
            time.sleep(0.02)
        if failure:
            raise failure[0]
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
        if check and process.returncode:
            err = bytes(errors[:400]).decode("utf-8", "replace")
            raise RuntimeError(f"command failed ({process.returncode}): {err}")
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


def run(argv, stdin="", *, timeout, env=None, cwd=None, max_output=MAX_OUTPUT, check=True):
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("command must be a nonempty argv list")
    if timeout is None or timeout <= 0:
        raise ValueError("timeout must be positive")
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
                result = _run_posix(process, timeout, max_output, pgid, check)
            else:
                result = _run_windows(process, timeout, max_output, pgid, check)
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise CommandTimedOut(f"command timed out after {timeout}s") from exc
        finally:
            try:
                atexit.unregister(cleanup)
            except Exception:
                pass
        return result
