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
import subprocess

from mindie_knowledge.releases import (
    select_release, validate_compatibility, validate_release_metadata,
)
from mindie_knowledge.releases_io import fetch_releases, verify_release_source

REPOSITORY = "mindie-agent/mindie-agent-kimi"
HARNESS = "kimi-code"


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
    ).stdout.strip()
    selected = select_release(
        fetch_releases(REPOSITORY), repository=REPOSITORY, harness_name=HARNESS,
        harness_version=version, installed_version=installed_version,
    )
    if selected["candidate"] is not None:
        verify_release_source(
            selected["candidate"]["metadata"], repository=REPOSITORY, harness_name=HARNESS,
        )
    selected["harness_version"] = version
    return selected


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
    result = (metadata(args.source, args.revision) if args.command == "build"
              else select_candidate(args.kimi_bin, args.installed_version))
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
