#!/usr/bin/env python3
"""Fail before the suite if an installed runtime pin is wrong."""

from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]
_PIN = re.compile(
    r"([A-Za-z0-9_.-]+) @ git\+https://github.com/mindie-agent/\S+@([0-9a-f]{40})"
)


def fail(message: str) -> None:
    print("setup: " + message, file=sys.stderr)
    raise SystemExit(2)


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
    pins: dict[str, str] = {}
    for name in ("runtime-requirements.txt", "test-requirements.txt",
                 "runtime-active-overrides.txt"):
        text = (REPO / name).read_text(encoding="utf-8")
        found = _PIN.findall(text)
        if name == "runtime-requirements.txt" and not found:
            fail("runtime-requirements.txt declares no exact commit pins")
        pins.update(dict(found))
    if not pins:
        fail("candidate runtime declares no exact commit pins")
    for name, required in pins.items():
        actual = installed_commit(name)
        if actual != required:
            fail(f"installed {name} commit {actual} != required {required}")
        print(f"{name} {actual}")


if __name__ == "__main__":
    main()
