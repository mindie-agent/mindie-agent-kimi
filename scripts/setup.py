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
import shutil
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
_PROBE_TEMPLATE = """
import importlib
missing = []
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception as exc:
        missing.append(f"{{name}} ({{type(exc).__name__}}: {{exc}})")
if not missing:
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop import cli
    if not callable(getattr(Engine, "stop_if_idle", None)):
        missing.append("core engine lacks the stop_if_idle API")
    if not callable(getattr(cli, "load_transcript_adapter", None)):
        missing.append("core cli lacks load_transcript_adapter")
if not missing:
    try:
        module = cli.load_transcript_adapter({{"transcript_adapter": {parser!r}}})
        if module is None:
            missing.append("transcript adapter did not load")
    except Exception as exc:
        missing.append(f"transcript adapter load failed ({{type(exc).__name__}}: {{exc}})")
print("MISSING: " + "; ".join(missing) if missing else "OK")
"""


def probe_script(parser: str) -> str:
    return _PROBE_TEMPLATE.format(modules=list(PROBE_MODULES), parser=parser)


PROBE_SCRIPT = probe_script(
    str((PLUGIN_ROOT / "scripts" / "transcript.py").resolve()))


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


BOOTSTRAP_ITEMS = (
    "scripts",
    "skills",
    "commands",
    "kimi.plugin.json",
    "runtime-requirements.txt",
)
BOOTSTRAP_COMPLETE = ".bootstrap-complete"


def _bootstrap_complete(root: Path) -> bool:
    if not (root / BOOTSTRAP_COMPLETE).is_file():
        return False
    return all((root / name).exists() for name in BOOTSTRAP_ITEMS)


def stage_retained_bootstrap(dest: Path, source: Path) -> Path:
    """Copy plugin code into an installation-owned generation.

    Staging is atomic: items are copied into a temp directory, a completion
    marker is written only after every BOOTSTRAP_ITEMS path exists, then the
    temp directory is renamed to dest. An already-complete retained generation
    is reused and never overwritten. An unknown or incomplete dest fails
    closed. Only this function's failed temp staging is cleaned.
    """
    dest = dest.expanduser().absolute()
    source = source.expanduser().absolute()
    if dest == source:
        raise SystemExit("refusing to use the source checkout as live generation")
    if dest.exists():
        if _bootstrap_complete(dest):
            return dest
        raise SystemExit(
            f"incomplete or unknown retained bootstrap, refusing to overwrite: {dest}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{dest.name}.staging-{secrets.token_hex(8)}"
    try:
        staging.mkdir()
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        for name in BOOTSTRAP_ITEMS:
            src = source / name
            if not src.exists():
                raise SystemExit("retained bootstrap is missing required plugin files")
            if src.is_dir():
                shutil.copytree(src, staging / name, ignore=ignore)
            else:
                shutil.copy2(src, staging / name)
        missing = [name for name in BOOTSTRAP_ITEMS if not (staging / name).exists()]
        if missing:
            raise SystemExit("retained bootstrap is missing required plugin files")
        (staging / BOOTSTRAP_COMPLETE).write_text("ok\n")
        os.replace(staging, dest)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return dest


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


def build_bootstrap_runtime(domain_root: Path) -> str:
    """Fresh install builds its own pinned venv from runtime-requirements.txt;
    no hand-created venv required."""
    venv = domain_root / "bootstrap-venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists():
        return str(python)
    env = dict(
        os.environ,
        PIP_RETRIES="0",
        PIP_NO_INPUT="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1",
        GIT_TERMINAL_PROMPT="0",
    )
    run([sys.executable, "-m", "venv", str(venv)], "", timeout=180)
    run(
        [str(python), "-m", "pip", "install", "-r",
         str(PLUGIN_ROOT / "runtime-requirements.txt")],
        "",
        timeout=600,
        max_output=512 * 1024,
        env=env,
    )
    return str(python)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-python", default=None,
                        help="operator override; default builds a pinned venv")
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
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required")
    community = community_settings(args, parser)
    config = args.config.expanduser().absolute()
    domain_root = args.root.expanduser().absolute() / "kimi"
    if args.knowledge_python:
        python = str(Path(args.knowledge_python).expanduser())
        if not os.path.isabs(python):
            python = str(Path(python).absolute())
        probe_runtime(python)
    else:
        python = build_bootstrap_runtime(domain_root)
        probe_runtime(python)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.domain):
        parser.error("invalid domain name")
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
    admission = domain_root / "admission.sqlite3"
    state_dir = domain_root / "adapter-state"
    bootstrap_root = state_dir / "update" / "generations" / "bootstrap"
    bootstrap_root = stage_retained_bootstrap(bootstrap_root, PLUGIN_ROOT)
    transcript = bootstrap_root / "scripts" / "transcript.py"
    organizer = bootstrap_root / "scripts" / "organizer.py"
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
        state_dir=str(state_dir),
    )
    if args.kimi_home:
        adapter_value["kimi_home"] = str(args.kimi_home.expanduser().absolute())
    else:
        adapter_value["kimi_home"] = str(
            Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code")
            .expanduser().absolute()
        )
    if args.update_remote:
        adapter_value["update_remote"] = args.update_remote
    write_private(config, adapter_value)
    sharing = write_community(community_config, community)
    import genstate
    import updater

    # Bootstrap gets a REAL retained host package on the stable front, so
    # first native install and any later rollback target an actual package.
    bootstrap_package = updater.build_host_package(
        bootstrap_root, adapter_value, "bootstrap",
        package_dir=genstate.update_dir(adapter_value) / "bootstrap-package",
        config_file=config,
    )
    genstate.write_current(
        {
            "generation": str(bootstrap_root),
            "python": python,
            "adapter_config": str(config),
            "sha": None,
        },
        adapter_value,
    )
    scheduled = "skipped"
    if not args.no_schedule:
        try:
            import contextlib
            import io

            with contextlib.redirect_stdout(io.StringIO()):
                code = updater.install_schedule(config)
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
                native_package=str(bootstrap_package),
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
