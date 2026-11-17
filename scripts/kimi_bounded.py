"""Bounded subprocess adapter for `mindie-agent-kimi` backed by `mindie_harness.process`.

[Role]
Delegates Kimi subprocess supervision (`run`) to `mindie_harness.process.run_owned`
(Win32 `CREATE_NO_WINDOW` + nested Job Object before `ResumeThread`; POSIX `start_new_session`
+ `os.killpg`), preserving Kimi's exception contracts (`CommandCancelled`, `CommandTimedOut`,
`OutputLimitExceeded`), `MINDIE_MAINTENANCE_GROUP=1` inherited-group mode, and static
transport error classification (`classify_transport_exception`, `classify_transport_text`).

[Invariants]
- Zero `CREATE_NEW_CONSOLE` or `DETACHED_PROCESS` flags on Windows (`CREATE_NO_WINDOW` only).
- Never retries failed or timed-out commands; never swallows `TimeoutError` inside `except OSError:`.
- Scrubs credentials from bounded stderr `reason` strings while keeping shared diagnostics free-text-free.

[Timeouts & Locks]
- Requires positive `timeout`; delegates process-tree drain and deadline enforcement to `Deadline`.

[Side-Effect Policy]
- Spawns and reaps owned child process trees; mutates no global environment or working directory.
"""
from __future__ import annotations

import http.client
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import ssl
import subprocess
import tempfile
import time
import urllib.error

try:
    from mindie_harness.process import (
        Deadline,
        OutputLimitError,
        ProcessCancelledError,
        ProcessExecutionError,
        ProcessSpec,
        run_owned,
    )
except ImportError:
    Deadline = None
    OutputLimitError = ()
    ProcessCancelledError = ()
    ProcessExecutionError = ()
    ProcessSpec = None
    run_owned = None

POSIX = os.name == "posix"
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MAX_OUTPUT = 256 * 1024
REMOTE_MAX_OUTPUT = 32 * 1024 * 1024
_CHUNK = 8192
_STREAM_EDGE = 4096
_REASON_LIMIT = 360


class CommandCancelled(RuntimeError):
    """The caller cancelled this attempt; owned children are reaped."""

    category = "cancelled"
    reason_code = "child_process_cancelled"


class CommandTimedOut(RuntimeError):
    """The command did not finish before the absolute deadline."""

    def __init__(self, message, *args, category=None, retry_after=None):
        super().__init__(message, *args)
        self.category = category
        self.retry_after = retry_after


class OutputLimitExceeded(ValueError, RuntimeError):
    """Captured output grew past the configured byte bound."""


_RETRY_AFTER = re.compile(r"retry-after\s*[:=]\s*(\d{1,6})\b", re.I)
_HTTP_STATUS = re.compile(
    r"(?:http\s*/\s*1\.[01]\s+|status(?:\s+code)?\s*[:=]\s*|"
    r"error\s*[:=]\s*|returned error:\s*|http error\s+|http\s+)(\d{3})\b",
    re.I,
)
_CERT_PHRASES = (
    "certificate verify failed", "sslcertverificationerror", "certificate has expired",
    "self-signed certificate", "self signed certificate", "unable to get local issuer certificate",
    "certificate_verify_failed", "ssl: certificate", "ssl certificate problem", "unknown ca", "curl: (60)",
    "invalid peer certificate", "alert bad certificate", "alert certificate expired",
    "alert certificate revoked", "alert certificate unknown",
    "sec_e_untrusted_root", "sec_e_cert_expired", "sec_e_wrong_principal", "crypt_e_no_revocation_check",
)
_RESOLVER_PHRASES = (
    "resolutionimpossible", "conflicting dependencies", "package versions have conflicting",
    "the conflict is caused by", "resolver conflict",
    "requirements contain conflicting urls", "conflicting urls",
    "no solution found when resolving dependencies",
    "no solution found when resolving package dependencies",
    "unsatisfiable requirements", "requirements are unsatisfiable",
    "version solving failed", "because your project depends on",
)
_HASH_PHRASES = ("these packages do not match the hashes", "does not match the hashes", "hash mismatch")
_GENERIC_CONTENT_PHRASES = ("no matching distribution", "could not find a version that satisfies")
_AUTH_PHRASES = (
    "authentication failed", "could not read username", "terminal prompts disabled",
    "permission denied (publickey)", "invalid credentials", "http basic: access denied",
    "support for password authentication was removed", "authentication required", "invalid username or password",
)
_RATE_PHRASES = ("rate limit", "too many requests", "secondary rate limit")
_HOOK_PHRASES = ("hook trust", "requires native trust", "changed hooks require")
_PREREQUISITE_PHRASES = (
    "no space left on device", "disk quota exceeded", "read-only file system",
    "too many open files", "file system is full", "not enough space", "disk full",
    "os error 28", "errno 28", "enospc",
    "failed to create virtual environment", "failed to initialize cache",
    "cache directory is not writable", "failed to write to the cache",
    "failed to read from the cache", "failed to create cache",
)
_CONNECT_PHRASES = (
    "could not resolve host", "temporary failure in name resolution", "name or service not known",
    "nodename nor servname", "temporary failure resolving", "network is unreachable",
    "connection timed out", "connection reset by peer", "connection refused", "connection aborted",
    "failed to connect", "operation timed out", "read operation timed out", "timed out",
    "curl: (6)", "curl: (7)", "curl: (28)", "curl: (35)", "curl: (56)", "newconnectionerror",
    "remote end closed connection", "unexpected eof", "unexpected_eof_while_reading",
    "eof occurred in violation of protocol", "ssl_error_syscall", "ssl_error_eof", "ssl_error_zero_return",
    "tlsv1 alert", "sslv3 alert", "gnutls_handshake() failed",
    "the tls connection was non-properly terminated", "without sending tls close_notify",
    "tls handshake eof", "schannel: failed to receive handshake", "sec_e_context_expired",
    "sec_e_message_altered", "incompleteread(", "connection broken", "bad gateway",
    "service unavailable", "gateway time-out", "gateway timeout", "error sending request",
    "dns error", "recv failure", "send failure", "max retries exceeded", "proxy connect aborted",
)
_URL_USERINFO = re.compile(r"(?i)\b(https?://)[^/\s@]+@")
_AUTH_HEADER = re.compile(r"(?i)\b(authorization|proxy-authorization)\s*:\s*[^\r\n]+")
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+")
_SECRET_FIELD = re.compile(
    r"(?i)\b(access_token|refresh_token|id_token|token|password|passwd|secret|api[_-]?key)"
    r"\s*[:=]\s*[^\s&,;]+"
)
_QUERY_SECRET = re.compile(
    r"(?i)([?&](?:access_token|refresh_token|id_token|token|api[_-]?key|password|secret)=)[^&\s]+"
)


def _has_phrase(text, phrases):
    return any(phrase in text for phrase in phrases)


def _finite_delay(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        return None
    return number


def _is_cert_verification_error(exc) -> bool:
    if isinstance(exc, ssl.SSLCertVerificationError):
        return True
    return isinstance(exc, ssl.SSLError) and getattr(exc, "reason", None) == "CERTIFICATE_VERIFY_FAILED"


def classify_transport_exception(exc):
    """Classify typed stdlib HTTP/TLS/socket transport errors without free-text parsing."""
    if exc is None or isinstance(exc, TimeoutError):
        return None
    if isinstance(exc, urllib.error.HTTPError):
        headers = getattr(exc, "headers", None)
        raw_retry = headers.get("Retry-After") if headers is not None and hasattr(headers, "get") else None
        retry_after = _finite_delay(raw_retry) if raw_retry is not None else None
        return ("rate_limited" if exc.code == 429 else "temporary_network", retry_after)
    if _is_cert_verification_error(exc):
        return ("certificate", None)
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if _is_cert_verification_error(reason) or _is_cert_verification_error(getattr(exc, "__cause__", None)):
            return ("certificate", None)
        return ("temporary_network", None)
    if isinstance(exc, ssl.SSLError):
        if _is_cert_verification_error(getattr(exc, "__cause__", None)):
            return ("certificate", None)
        return ("temporary_network", None)
    if isinstance(
        exc,
        (
            http.client.InvalidURL,
            http.client.ImproperConnectionState,
            http.client.NotConnected,
            http.client.UnimplementedFileMode,
        ),
    ):
        return None
    if isinstance(exc, (ConnectionError, http.client.HTTPException, socket.gaierror, socket.herror)):
        return ("temporary_network", None)
    return None


def classify_transport_text(text):
    if not isinstance(text, str) or not text.strip():
        return None
    sample = text[:65536]
    lower = sample.lower()
    match = _RETRY_AFTER.search(sample)
    retry_after = _finite_delay(match.group(1)) if match else None
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
    if _has_phrase(lower, _PREREQUISITE_PHRASES):
        return ("prerequisite", None)
    if (codes & {500, 502, 503, 504}) or _has_phrase(lower, _CONNECT_PHRASES):
        return ("temporary_network", retry_after)
    if _has_phrase(lower, _GENERIC_CONTENT_PHRASES):
        return ("bad_content", None)
    return None


def _scrub_reason_line(line: str) -> str:
    line = _AUTH_HEADER.sub(r"\1: <redacted>", line)
    line = _AUTH_SCHEME.sub(r"\1 <redacted>", line)
    line = _URL_USERINFO.sub(r"\1<redacted>@", line)
    line = _QUERY_SECRET.sub(r"\1<redacted>", line)
    return _SECRET_FIELD.sub(r"\1=<redacted>", line)


def _failure_reason(text_or_bytes) -> str | None:
    if not text_or_bytes:
        return None
    text = text_or_bytes if isinstance(text_or_bytes, str) else bytes(text_or_bytes).decode("utf-8", "replace")
    lines = [_scrub_reason_line(line.strip()) for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    selected = []
    for line in (lines[0], *[item for item in lines if "http" in item.lower()][:4], lines[-1]):
        if line not in selected:
            selected.append(line)
    return " | ".join(selected)[:_REASON_LIMIT]


def _stream_edges(text: str) -> str:
    if not text:
        return ""
    return text if len(text) <= _STREAM_EDGE * 2 else text[:_STREAM_EDGE] + "\n" + text[-_STREAM_EDGE:]


def _decorate_error(exc, stdout: str, stderr: str, returncode, *, transport: bool, timed_out: bool):
    exc.mindie_output = stdout
    exc.mindie_stderr = stderr
    exc.returncode = returncode
    if not getattr(exc, "reason_code", None):
        exc.reason_code = "child_process_timed_out" if timed_out else "child_process_failed"
    if (reason := _failure_reason(stderr)) is not None:
        exc.reason = reason
    if transport:
        joined = "\n".join(p for p in (_stream_edges(stdout), _stream_edges(stderr)) if p)
        if (kind := classify_transport_text(joined)) is not None:
            exc.category, retry_after = kind
            if retry_after is not None:
                exc.retry_after = retry_after
        elif timed_out:
            exc.category = "temporary_network"
    return exc


def _run_inherited_posix(argv, data: bytes, *, timeout, env, cwd, max_output, check, cancel, transport, capture_result):
    """POSIX maintenance-group mode (`MINDIE_MAINTENANCE_GROUP=1`): keep child in parent pgrp."""
    with tempfile.TemporaryFile() as stream:
        if data:
            stream.write(data)
            stream.seek(0)
        proc = subprocess.Popen(
            list(argv), stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, cwd=cwd, start_new_session=False,
        )
        sel = selectors.DefaultSelector()
        sel.register(proc.stdout, selectors.EVENT_READ, "out")
        sel.register(proc.stderr, selectors.EVENT_READ, "err")
        deadline = time.monotonic() + timeout
        out_buf, err_buf = bytearray(), bytearray()
        total = 0
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    raise CommandCancelled("command cancelled")
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise TimeoutError("deadline exceeded")
                if proc.poll() is not None and not sel.get_map():
                    break
                if sel.get_map():
                    for key, _ in sel.select(min(0.05, rem)):
                        chunk = os.read(key.fileobj.fileno(), _CHUNK)
                        if not chunk:
                            sel.unregister(key.fileobj)
                            continue
                        dest = out_buf if key.data == "out" else err_buf
                        dest.extend(chunk[: max(0, max_output - total)])
                        total += len(chunk)
                        if total > max_output:
                            raise OutputLimitExceeded("output exceeds the bound")
                    continue
                try:
                    proc.wait(timeout=min(0.05, rem))
                except subprocess.TimeoutExpired:
                    continue
                break
            stdout_s = out_buf.decode("utf-8", "replace")
            stderr_s = err_buf.decode("utf-8", "replace")
            if check and proc.returncode:
                raise _decorate_error(
                    RuntimeError(f"command failed ({proc.returncode})"),
                    stdout_s, stderr_s, proc.returncode, transport=transport, timed_out=False,
                )
            if capture_result:
                return subprocess.CompletedProcess(list(argv), proc.returncode, stdout_s, stderr_s)
            return stdout_s
        except (CommandCancelled, OutputLimitExceeded, TimeoutError) as exc:
            _decorate_error(
                exc, out_buf.decode("utf-8", "replace"), err_buf.decode("utf-8", "replace"),
                proc.poll(), transport=transport, timed_out=isinstance(exc, TimeoutError),
            )
            if isinstance(exc, TimeoutError):
                err = CommandTimedOut(
                    f"command timed out after {timeout}s",
                    category=getattr(exc, "category", None),
                    retry_after=getattr(exc, "retry_after", None),
                )
                for attr in ("mindie_output", "mindie_stderr", "returncode", "reason", "reason_code"):
                    if hasattr(exc, attr):
                        setattr(err, attr, getattr(exc, attr))
                raise err from exc
            raise
        finally:
            try:
                if proc.poll() is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                proc.wait(timeout=2.0)
            except TimeoutError:
                pass
            except (OSError, subprocess.SubprocessError):
                pass
            sel.close()
            proc.stdout.close()
            proc.stderr.close()


def _kill_tree(process=None, pgid=None) -> None:
    """Post-run local process-tree cleanup hook; cleanup errors never mask a completed run."""
    return None


def _truncate_utf8(text: str, limit: int) -> str:
    raw = text.encode("utf-8", "replace")
    return text if len(raw) <= limit else raw[: max(0, limit)].decode("utf-8", "ignore")


class _CompletedResult(subprocess.CompletedProcess):
    cleanup_error: str | None = None


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
    capture_result=False,
):
    if (
        not isinstance(argv, (list, tuple))
        or not argv
        or not isinstance(argv[0], str)
        or not argv[0]
        or any(not isinstance(item, str) for item in argv)
    ):
        raise ValueError("command must be a nonempty argv list")
    if timeout is None or timeout <= 0:
        raise ValueError("timeout must be positive")
    if cancel is not None and cancel.is_set():
        raise CommandCancelled("command cancelled")
    data = stdin.encode("utf-8") if isinstance(stdin, str) else (stdin or b"")

    if POSIX and os.environ.get("MINDIE_MAINTENANCE_GROUP") == "1" and os.getpgrp() == os.getpid():
        return _run_inherited_posix(
            argv, data, timeout=timeout, env=env, cwd=cwd, max_output=max_output,
            check=check, cancel=cancel, transport=transport, capture_result=capture_result,
        )

    if run_owned is None or ProcessSpec is None or Deadline is None or any(item == "" for item in argv[1:]):
        return _run_stdlib_minimal(
            argv, data, timeout=timeout, env=env, cwd=cwd, max_output=max_output,
            check=check, cancel=cancel, transport=transport, capture_result=capture_result,
        )

    cap = max(1024, int(max_output))
    spec = ProcessSpec(
        argv=tuple(argv),
        cwd=Path(cwd) if cwd is not None else None,
        env=env,
        stdin_bytes=bytes(data),
        max_output_bytes=cap + 65536,
        mcp_response_cap_bytes=max(256, min(cap, REMOTE_MAX_OUTPUT)),
        memory_limit_bytes=None,
        allowed_returncodes=(0,) if check else None,
    )
    dl = Deadline.from_timeout(float(timeout), cleanup_grace=min(2.0, max(0.05, float(timeout) * 0.25)))
    cleanup_err: str | None = None
    try:
        outcome = run_owned(spec, deadline=dl, cancel=cancel)
    except ProcessCancelledError as exc:
        err = CommandCancelled("command cancelled")
        _decorate_error(err, exc.outcome.stdout, exc.outcome.stderr, exc.outcome.returncode, transport=transport, timed_out=False)
        raise err from None
    except OutputLimitError as exc:
        err = OutputLimitExceeded("output exceeds the bound")
        _decorate_error(
            err,
            _truncate_utf8(exc.outcome.stdout, max_output),
            _truncate_utf8(exc.outcome.stderr, max(0, max_output - len(_truncate_utf8(exc.outcome.stdout, max_output).encode("utf-8")))),
            exc.outcome.returncode,
            transport=transport,
            timed_out=False,
        )
        raise err from None
    except ProcessExecutionError as exc:
        err = RuntimeError(f"command failed ({exc.outcome.returncode})")
        _decorate_error(err, exc.outcome.stdout, exc.outcome.stderr, exc.outcome.returncode, transport=transport, timed_out=False)
        raise err from None
    except TimeoutError as exc:
        out_s = getattr(getattr(exc, "outcome", None), "stdout", "")
        err_s = getattr(getattr(exc, "outcome", None), "stderr", "")
        rc = getattr(getattr(exc, "outcome", None), "returncode", None)
        _decorate_error(exc, out_s, err_s, rc, transport=transport, timed_out=True)
        err = CommandTimedOut(
            f"command timed out after {timeout}s",
            category=getattr(exc, "category", "temporary_network" if transport else None),
            retry_after=getattr(exc, "retry_after", None),
        )
        for attr in ("mindie_output", "mindie_stderr", "returncode", "reason", "reason_code"):
            if hasattr(exc, attr):
                setattr(err, attr, getattr(exc, attr))
        raise err from exc
    except Exception as exc:
        if getattr(exc, "stage", None) == "spawn" and isinstance(exc.__cause__, OSError):
            raise exc.__cause__ from None
        raise
    finally:
        try:
            _kill_tree(None, None)
        except Exception as c_exc:  # noqa: BLE001
            cleanup_err = f"{type(c_exc).__name__}: {c_exc}"

    out_bytes = len(outcome.stdout.encode("utf-8"))
    err_bytes = len(outcome.stderr.encode("utf-8"))
    if out_bytes + err_bytes > max_output:
        err = OutputLimitExceeded("output exceeds the bound")
        kept_out = _truncate_utf8(outcome.stdout, max_output)
        kept_err = _truncate_utf8(outcome.stderr, max(0, max_output - len(kept_out.encode("utf-8"))))
        _decorate_error(err, kept_out, kept_err, outcome.returncode, transport=transport, timed_out=False)
        if cleanup_err:
            err.cleanup_error = cleanup_err
        raise err
    if cancel is not None and cancel.is_set():
        raise CommandCancelled("command cancelled")
    if capture_result:
        res = _CompletedResult(list(argv), outcome.returncode, outcome.stdout, outcome.stderr)
        if cleanup_err:
            res.cleanup_error = cleanup_err
        return res
    return outcome.stdout


def _run_stdlib_minimal(argv, data: bytes, *, timeout, env, cwd, max_output, check, cancel, transport, capture_result):
    """Minimal cold-bootstrap fallback when `mindie_harness` is not installed yet (`CREATE_NO_WINDOW`)."""
    with tempfile.TemporaryFile() as stream:
        if data:
            stream.write(data)
            stream.seek(0)
        kwargs = dict(
            stdin=stream, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, cwd=cwd,
        )
        if POSIX:
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = CREATE_NO_WINDOW | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        proc = subprocess.Popen(list(argv), **kwargs)
        try:
            out_b, err_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            if POSIX:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except OSError:
                    proc.kill()
            else:
                proc.kill()
            proc.wait(timeout=2.0)
            out_s = (exc.stdout or b"").decode("utf-8", "replace")
            err_s = (exc.stderr or b"").decode("utf-8", "replace")
            err = CommandTimedOut(f"command timed out after {timeout}s", category="temporary_network" if transport else None)
            _decorate_error(err, out_s, err_s, proc.returncode, transport=transport, timed_out=True)
            raise err from exc
        if len(out_b) + len(err_b) > max_output:
            err = OutputLimitExceeded("output exceeds the bound")
            _decorate_error(err, out_b[:max_output].decode("utf-8", "replace"), err_b.decode("utf-8", "replace"), proc.returncode, transport=transport, timed_out=False)
            raise err
        out_s, err_s = out_b.decode("utf-8", "replace"), err_b.decode("utf-8", "replace")
        if check and proc.returncode:
            raise _decorate_error(RuntimeError(f"command failed ({proc.returncode})"), out_s, err_s, proc.returncode, transport=transport, timed_out=False)
        if capture_result:
            return subprocess.CompletedProcess(list(argv), proc.returncode, out_s, err_s)
        return out_s
