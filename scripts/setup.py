#!/usr/bin/env python3
"""Configure MindIE-owned files for the Kimi adapter. Default sharing OFF.

Does not write ~/.kimi-code. Sharing can be configured after install.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import sys
import time

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run
from paths import PLUGIN_ROOT, config_path as default_config_path

PROBE_MODULES = (
    "mindie_knowledge.loop.cli",
    "mindie_knowledge.loop.activation",
    "mindie_knowledge.loop.engine",
    "remote_dev.mcp.tools",
)
PROBE_SCRIPT = """
import importlib
missing = []
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{{name}} ({{type(exc).__name__}}: {{exc}})")
print("MISSING: " + "; ".join(missing) if missing else "OK")
""".format(modules=list(PROBE_MODULES))


def probe_runtime(python):
    try:
        output = run([python, "-c", PROBE_SCRIPT], "", timeout=15)
    except Exception as exc:
        raise SystemExit(
            f"knowledge runtime probe failed in {python}: {type(exc).__name__}: {str(exc)[:200]}"
        )
    if not output.strip().endswith("OK"):
        raise SystemExit(
            f"{python} is missing pinned dependencies: {output.strip()}. "
            "Install runtime-requirements.txt first."
        )


def write_private(path, value, *, replace=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_TRUNC if replace else os.O_EXCL)
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def community_settings(args, parser):
    selected = any(
        getattr(args, key)
        for key in (
            "community_repository",
            "community_project_root",
            "community_account",
            "community_fork",
            "community_visibility",
        )
    )
    if not selected:
        if args.community_branch is not None:
            parser.error("--community-branch requires --community-repository")
        return None
    if not args.community_repository:
        parser.error("community sharing requires --community-repository owner/repo")
    if not args.community_project_root:
        parser.error("community sharing requires at least one --community-project-root")
    if args.community_visibility != "public":
        parser.error("community sharing requires --community-visibility public")
    roots = []
    for root in args.community_project_root:
        canonical = str(Path(root).expanduser().resolve())
        if not Path(canonical).is_dir():
            parser.error("community project root does not exist: " + canonical)
        if canonical not in roots:
            roots.append(canonical)
    data = dict(
        schema="mindie-community-config/1",
        enabled=True,
        generation=secrets.token_hex(16),
        enabled_at=time.time(),
        repository=args.community_repository,
        branch=args.community_branch or "main",
        project_roots=roots,
        idle_seconds=300,
        visibility="public",
    )
    if args.community_account:
        data["account"] = args.community_account
    if args.community_fork:
        data["fork"] = args.community_fork
    return data


def write_community(path, community):
    if community is None:
        write_private(
            path,
            dict(
                schema="mindie-community-config/1",
                enabled=False,
                generation=secrets.token_hex(16),
                enabled_at=None,
                repository="local/unconfigured",
                branch="main",
                project_roots=[],
                idle_seconds=300,
            ),
        )
        return "off"
    write_private(path, community, replace=path.exists())
    return "enabled"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-python", required=True)
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
        / "mindie-agent",
    )
    parser.add_argument("--domain", default="vllm-ascend")
    parser.add_argument("--no-public-feed", action="store_true")
    parser.add_argument("--community-repository", metavar="OWNER/REPO")
    parser.add_argument("--community-project-root", action="append")
    parser.add_argument("--community-branch")
    parser.add_argument("--community-account")
    parser.add_argument("--community-fork")
    parser.add_argument("--community-visibility", choices=["public"])
    parser.add_argument("--kimi-home", type=Path,
                        help="explicit Kimi home for native plugin install/update")
    parser.add_argument("--update-remote", default=None,
                        help="git remote tracked for automatic updates")
    parser.add_argument("--no-schedule", action="store_true",
                        help="do not register the automatic update check")
    args = parser.parse_args()
    python = str(Path(args.knowledge_python).expanduser())
    if not os.path.isabs(python):
        python = str(Path(python).absolute())
    probe_runtime(python)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.domain):
        parser.error("invalid domain name")
    community = community_settings(args, parser)
    config = args.config.expanduser().absolute()
    engine_config = config.with_name(config.stem + ".engine.json")
    community_config = config.with_name(config.stem + ".community.json")
    if config.exists() or engine_config.exists():
        if community is None:
            parser.error(
                "configuration already exists; pass --community-* to configure sharing"
            )
        sharing = write_community(community_config, community)
        print(json.dumps(dict(config=str(config), sharing=sharing, updated="community"), indent=2))
        return
    domain_root = args.root.expanduser().absolute() / "kimi"
    admission = domain_root / "admission.sqlite3"
    transcript = (PLUGIN_ROOT / "scripts" / "transcript.py").resolve()
    organizer = (PLUGIN_ROOT / "scripts" / "organizer.py").resolve()
    value = dict(
        root=str(domain_root),
        domain=args.domain,
        admission_path=str(admission),
        transcript_adapter=str(transcript),
        agent_command=[python, str(organizer)],
        community_config=str(community_config),
    )
    if args.domain == "vllm-ascend" and not args.no_public_feed:
        value["feeds"] = [
            dict(
                repository="mindie-agent/knowledge-vllm-ascend",
                ref="main",
                domain="vllm-ascend",
                interval_seconds=300,
            )
        ]
    write_private(engine_config, value)
    adapter_value = dict(
        python=python,
        engine_config=str(engine_config),
        community_config=str(community_config),
        state_dir=str(domain_root / "adapter-state"),
    )
    if args.kimi_home:
        adapter_value["kimi_home"] = str(args.kimi_home.expanduser().absolute())
    if args.update_remote:
        adapter_value["update_remote"] = args.update_remote
    write_private(config, adapter_value)
    sharing = write_community(community_config, community)
    import genstate

    genstate.write_current(
        {"generation": str(PLUGIN_ROOT), "python": python, "sha": None},
        adapter_value,
    )
    scheduled = "skipped"
    if not args.no_schedule:
        os.environ["MINDIE_KIMI_CONFIG"] = str(config)
        try:
            import contextlib
            import io

            import updater

            with contextlib.redirect_stdout(io.StringIO()):
                code = updater.install_schedule()
            scheduled = "registered" if code == 0 else "manual"
        except Exception as exc:
            scheduled = f"manual ({type(exc).__name__}: {str(exc)[:120]})"
    print(
        json.dumps(
            dict(
                config=str(config),
                engine_config=str(engine_config),
                community_config=str(community_config),
                admission_path=str(admission),
                domain=args.domain,
                sharing=sharing,
                update_schedule=scheduled,
                plugin_install=(
                    "Native install: POST /api/v1/plugins {source: <host-package>} "
                    "in the selected Kimi home (scripts/install_kimi_plugin.py). "
                    "Automatic updates: scripts/updater.py check (scheduled), "
                    "status via scripts/updater.py status, recovery via "
                    "scripts/updater.py recover. See docs/update.md."
                ),
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
