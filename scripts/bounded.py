"""Managed child with process-group kill and bounded output."""

from __future__ import annotations

import os
import signal
import subprocess

MAX_OUTPUT = 256 * 1024


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
    try:
        stdout, stderr = process.communicate(data, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=1)
        except subprocess.TimeoutExpired:
            _kill_tree(process, force=True)
            stdout, stderr = process.communicate(timeout=1)
        raise RuntimeError(
            f"command timed out after {timeout}s: {(stderr or b'')[:200].decode('utf-8', 'replace')}"
        )
    if stdout is None:
        stdout = b""
    if stderr is None:
        stderr = b""
    if len(stdout) > max_output or len(stderr) > max_output:
        raise RuntimeError("command output exceeds the bound")
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
