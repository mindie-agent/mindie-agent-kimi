#!/usr/bin/env python3
"""Fail before the suite if the peer checkout or an installed pin is wrong.

git archive only needs the commit object. HEAD ancestry is not required.
This does not search sibling directories or a production install.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPO = Path(__file__).resolve().parents[1]
PEER = ("MINDIE_PEER_CC_REPO", "3e36a285e2effe47bc442079a4ed7f42ced9caaf")
_PIN = re.compile(
    r"([A-Za-z0-9_.-]+) @ git\+https://github.com/mindie-agent/\S+@([0-9a-f]{40})"
)


def fail(message: str) -> None:
    print("setup: " + message, file=sys.stderr)
    raise SystemExit(2)


def require_commit(env_name: str, commit: str) -> None:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        fail(
            f"{env_name} is unset. Point it at a git checkout that contains {commit}. "
            "This does not search sibling directories."
        )
    path = Path(raw).expanduser()
    if not path.is_dir():
        fail(f"{env_name} is not a directory: {path}")
    kind = subprocess.run(
        ["git", "-C", str(path), "cat-file", "-t", commit],
        capture_output=True, text=True, check=False,
    )
    if kind.returncode != 0 or kind.stdout.strip() != "commit":
        detail = (kind.stderr or kind.stdout).strip()
        fail(f"{env_name}={path} does not contain commit {commit}. {detail}".rstrip())


def installed_commit(dist_name: str) -> str:
    try:
        text = importlib.metadata.distribution(dist_name).read_text("direct_url.json")
    except importlib.metadata.PackageNotFoundError:
        fail(f"{dist_name} is not installed")
    if not text:
        fail(f"{dist_name} has no direct_url.json")
    commit = (json.loads(text).get("vcs_info") or {}).get("commit_id")
    if not isinstance(commit, str) or not commit:
        fail(f"{dist_name} direct_url.json has no vcs commit")
    return commit


def main() -> None:
    env_name, commit = PEER
    require_commit(env_name, commit)
    print(f"{env_name} contains {commit}")
    found = _PIN.findall((REPO / "runtime-requirements.txt").read_text(encoding="utf-8"))
    if not found:
        fail("runtime-requirements.txt declares no exact commit pins")
    for name, required in found:
        actual = installed_commit(name)
        if actual != required:
            fail(f"installed {name} commit {actual} != required {required}")
        print(f"{name} {actual}")


if __name__ == "__main__":
    main()
