"""Managed child with process-group kill and bounded output.

The output bound is enforced DURING execution: reader threads cap each
stream and kill the owned process tree immediately once a stream exceeds
the budget. Timeout likewise terminates the whole process group.
Windows uses process.kill() only; child-tree termination there is not
verified.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading

MAX_OUTPUT = 256 * 1024
_CHUNK = 65536


def run(argv, stdin="", *, timeout, env=None, cwd=None, max_output=MAX_OUTPUT):
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("command must be a nonempty argv list")
    data = stdin.encode() if isinstance(stdin, str) else stdin
    spawn = dict(
        args=list(argv),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    if os.name != "nt":
        spawn["start_new_session"] = True
    process = subprocess.Popen(**spawn)
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    exceeded = []

    def reader(stream, name):
        total = 0
        while True:
            try:
                chunk = stream.read(_CHUNK)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            total += len(chunk)
            kept = buffers[name]
            if len(kept) < max_output:
                kept.extend(chunk[: max_output - len(kept)])
            if total > max_output and name not in exceeded:
                exceeded.append(name)
                _kill_tree(process)
                break
        try:
            stream.close()
        except OSError:
            pass

    threads = [
        threading.Thread(target=reader, args=(process.stdout, "stdout"), daemon=True),
        threading.Thread(target=reader, args=(process.stderr, "stderr"), daemon=True),
    ]
    for thread in threads:
        thread.start()
    try:
        if data:
            try:
                process.stdin.write(data)
            except (BrokenPipeError, OSError):
                pass
        process.stdin.close()
    except OSError:
        pass
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _kill_tree(process, force=True)
            process.wait(timeout=1)
        for thread in threads:
            thread.join(timeout=1)
        raise RuntimeError(f"command timed out after {timeout}s")
    for thread in threads:
        thread.join(timeout=2)
    if exceeded:
        _kill_tree(process, force=True)
        raise RuntimeError(f"command output exceeds the bound ({max_output} bytes)")
    stdout = bytes(buffers["stdout"])
    stderr = bytes(buffers["stderr"])
    if process.returncode != 0:
        err = stderr[:400].decode("utf-8", "replace")
        raise RuntimeError(f"command failed ({process.returncode}): {err}")
    return stdout.decode("utf-8", "replace")


def _kill_tree(process, force=False):
    sig = signal.SIGKILL if force else signal.SIGTERM
    try:
        if os.name != "nt":
            os.killpg(process.pid, sig)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass
