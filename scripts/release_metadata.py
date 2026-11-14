"""Kimi's declaration and official Release selection.

[Role] Build the published metadata and call the shared Release reader.
[Invariants] Only declared, compatible formal Releases can become candidates.
[Timeouts & Locks] The updater bounds this entire process; version probe is 10s.
[Side-Effect Policy] Read-only public catalog/source and native version reads.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mindie_knowledge.releases import (
    ReleaseValidationError, select_release, validate_compatibility, validate_release_metadata,
)
from mindie_knowledge.releases_io import ReleaseFetchError, fetch_releases, verify_release_source

REPOSITORY = "mindie-agent/mindie-agent-kimi"
HARNESS = "kimi-code"
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_HOST = frozenset({"FileNotFoundError", "CalledProcessError"})
_HOST_CODES = frozenset({"invalid_harness_version", "invalid_installed_version"})
_TIMEOUT = frozenset({"TimeoutExpired", "TimeoutError"})


class CatalogError(RuntimeError):
    """Expected catalog miss; static codes only, never raw feed text."""


def metadata(source: Path, revision: str) -> dict:
    declaration = json.loads((source / "harness-compatibility.json").read_text(encoding="utf-8"))
    manifest = json.loads((source / "kimi.plugin.json").read_text(encoding="utf-8"))
    return validate_release_metadata(dict(
        schema="mindie-plugin-release/1", version=manifest["version"],
        source=dict(repository=REPOSITORY, sha=revision),
        compatibility=validate_compatibility(declaration, harness_name=HARNESS),
    ), repository=REPOSITORY, harness_name=HARNESS)


def select_candidate(kimi_bin: str, installed_version: str) -> dict:
    version = subprocess.run(
        [kimi_bin, "--version"], check=True, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).stdout.strip()
    selected = select_release(
        fetch_releases(REPOSITORY, minimum_version=installed_version), repository=REPOSITORY, harness_name=HARNESS,
        harness_version=version, installed_version=installed_version,
    )
    if selected["candidate"] is not None:
        verify_release_source(
            selected["candidate"]["metadata"], repository=REPOSITORY, harness_name=HARNESS,
        )
    selected["harness_version"] = version
    return selected


def emit_select_error(exc) -> None:
    """Print one static catalog error object; do not echo feed or host text."""
    payload = {"error": {"type": type(exc).__name__}}
    code = getattr(exc, "code", None)
    if isinstance(code, str) and _CODE.fullmatch(code):
        payload["error"]["code"] = code
    status = getattr(exc, "status", None)
    if type(status) is int and 100 <= status <= 599:
        payload["error"]["status"] = status
    print(json.dumps(payload, sort_keys=True))
    raise SystemExit(1)


def _error_payload(raw):
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw.strip())
    except (ValueError, TypeError):
        return None
    error = data.get("error") if isinstance(data, dict) else None
    return error if isinstance(error, dict) else None


def typed_catalog_error(exc):
    """Rebuild typed catalog facts lost at the child process boundary."""
    payload = _error_payload(getattr(exc, "mindie_output", None))
    if payload is None:
        return exc
    kind = payload.get("type")
    code = payload.get("code")
    code = code if isinstance(code, str) and _CODE.fullmatch(code) else None
    if kind in _HOST or code in _HOST_CODES:
        error = FileNotFoundError("kimi binary is unavailable for release selection")
    elif kind in _TIMEOUT:
        error = TimeoutError("kimi version probe timed out")
    else:
        error = CatalogError(code or "release catalog unavailable")
        if code:
            error.code = code
        error.category = (
            "certificate" if code == "release_certificate_error"
            else "rate_limited" if payload.get("status") == 429
            else "temporary_network"
        )
    if (returncode := getattr(exc, "returncode", None)) is not None:
        error.returncode = returncode
    return error


def _transport_select_error(exc):
    if isinstance(exc, TimeoutError):
        return None
    from bounded import classify_transport_exception
    from mindie_knowledge.release_http import ReleaseResponseTooLarge

    if isinstance(exc, urllib.error.HTTPError):
        err = CatalogError("release_http_error")
        err.code = "release_http_error"
        err.status = exc.code
        return err
    if isinstance(exc, json.JSONDecodeError):
        err = CatalogError("release_catalog_invalid")
        err.code = "release_catalog_invalid"
        return err
    if isinstance(exc, ReleaseResponseTooLarge):
        err = CatalogError("release_transport_unavailable")
        err.code = "release_transport_unavailable"
        return err
    classified = classify_transport_exception(exc)
    if classified is None:
        return None
    category, _ = classified
    code = "release_certificate_error" if category == "certificate" else "release_transport_unavailable"
    err = CatalogError(code)
    err.code = code
    return err


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--revision", required=True)
    select = sub.add_parser("select")
    select.add_argument("--kimi-bin", required=True)
    select.add_argument("--installed-version", required=True)
    args = parser.parse_args()
    try:
        result = (metadata(args.source, args.revision) if args.command == "build"
                  else select_candidate(args.kimi_bin, args.installed_version))
    except (ReleaseFetchError, ReleaseValidationError, FileNotFoundError,
            subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        emit_select_error(exc)
    except Exception as exc:
        mapped = _transport_select_error(exc)
        if mapped is not None:
            emit_select_error(mapped)
        raise
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
