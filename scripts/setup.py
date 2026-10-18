#!/usr/bin/env python3
"""Initial setup and runtime bootstrap for `mindie-agent-kimi`.

[Role]
Builds or probes the pinned Python runtime (`runtime-requirements.txt`),
stages the retained `bootstrap` generation, writes `kimi.json`,
`kimi.engine.json`, and `mindie-community.json`, builds the initial native host
package, and registers the automatic update schedule.

[Invariants]
- Default community sharing is OFF unless `--community-*` flags are provided.
- Never mutates `~/.kimi-code` directly; native plugin registration is handled
  separately by `install_kimi_plugin.py`.
- Verifies SQLite >= 3.43 FTS5 `contentless_delete=1` and core module contracts
  via `probe_runtime`.

[Timeouts & Locks]
- Runtime probe timeout: `15s`; `ensurepip` probe: `15s`; `create_venv`: `180s`;
  `pip install`: `600s`.

[Side-Effect Policy]
- Creates `<root>/kimi/bootstrap-venv`, stages `<state_dir>/update/generations/bootstrap`,
  writes `kimi.json` / `kimi.engine.json` / `mindie-community.json` / `current.json`,
  and optionally installs the platform update schedule.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

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
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop import cli
    from mindie_knowledge.loop import locks
    from mindie_knowledge.loop import settings as _settings
    if not callable(getattr(Engine, "stop_if_idle", None)):
        missing.append("core engine lacks the stop_if_idle API")
    if not callable(getattr(cli, "load_transcript_adapter", None)):
        missing.append("core cli lacks load_transcript_adapter")
    if not callable(getattr(locks, "lock_held", None)):
        missing.append("core locks lacks lock_held")
    if not callable(getattr(_settings, "CommunityWriteContext", None)):
        missing.append("core settings lack the CommunityWriteContext API")
    for method in ("activate", "deactivate", "active_lease", "claim", "inspect", "check"):
        if not callable(getattr(Admission, method, None)):
            missing.append("core Admission lacks " + method)
if not missing:
    try:
        module = cli.load_transcript_adapter({{"transcript_adapter": {parser!r}}})
        if module is None:
            missing.append("transcript adapter did not load")
    except Exception as exc:
        missing.append(f"transcript adapter load failed ({{type(exc).__name__}}: {{exc}})")
"""

_FTS_PROBE = r"""
import sqlite3
_fts = None
try:
    _fts = sqlite3.connect(":memory:")
    _fts.execute("CREATE VIRTUAL TABLE probe USING fts5(body, content='', contentless_delete=1)")
    _fts.execute("INSERT INTO probe(rowid, body) VALUES (1, 'alpha')")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'alpha'").fetchall() != [(1,)]:
        raise RuntimeError("insert MATCH failed")
    _fts.execute("UPDATE probe SET body='beta' WHERE rowid=1")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'beta'").fetchall() != [(1,)]:
        raise RuntimeError("update MATCH failed")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'alpha'").fetchall():
        raise RuntimeError("stale MATCH survived update")
    _fts.execute("DELETE FROM probe WHERE rowid=1")
    if _fts.execute("SELECT rowid FROM probe WHERE probe MATCH 'beta'").fetchall():
        raise RuntimeError("delete MATCH failed")
except Exception as exc:
    missing.insert(0, (
        "sqlite " + sqlite3.sqlite_version
        + " lacks FTS5 contentless_delete=1 (SQLite >=3.43.0): "
        + type(exc).__name__ + ": " + str(exc)[:160]
    ))
finally:
    if _fts is not None:
        _fts.close()
"""

_PROBE_TAIL = """
print("MISSING: " + "; ".join(missing) if missing else "OK")
"""

BOOTSTRAP_ITEMS = (
    "scripts",
    "skills",
    "commands",
    "kimi.plugin.json",
    "runtime-requirements.txt",
)
BOOTSTRAP_COMPLETE = ".bootstrap-complete"


def probe_script(parser: str) -> str:
    return _PROBE_TEMPLATE.format(modules=list(PROBE_MODULES), parser=parser) + _FTS_PROBE + _PROBE_TAIL


PROBE_SCRIPT = probe_script(str((PLUGIN_ROOT / "scripts" / "transcript.py").resolve()))


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
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def community_settings(args, parser):
    selected = any(
        getattr(args, key)
        for key in (
            "community_repository", "community_project_root",
            "community_account", "community_fork", "community_visibility",
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


def _bootstrap_complete(root: Path) -> bool:
    return (root / BOOTSTRAP_COMPLETE).is_file() and all((root / name).exists() for name in BOOTSTRAP_ITEMS)


def stage_retained_bootstrap(dest: Path, source: Path) -> Path:
    dest = dest.expanduser().absolute()
    source = source.expanduser().absolute()
    if dest == source:
        raise SystemExit("refusing to use the source checkout as live generation")
    if dest.exists():
        if _bootstrap_complete(dest):
            return dest
        raise SystemExit(f"incomplete or unknown retained bootstrap, refusing to overwrite: {dest}")
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
        if any(not (staging / name).exists() for name in BOOTSTRAP_ITEMS):
            raise SystemExit("retained bootstrap is missing required plugin files")
        (staging / BOOTSTRAP_COMPLETE).write_text("ok\n")
        os.replace(staging, dest)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    return dest


def write_community(path, community, consent_config=None, python=None):
    from community_config import configure

    resolved_consent = consent_config or str(Path(path).with_name("mindie-consent.json").resolve())
    request = dict(community, consent_config=resolved_consent) if community is not None else None
    result = configure(path, request, python)
    if community is None and "consent_config" not in result:
        result = configure(path, dict(consent_config=resolved_consent), python)
    return "enabled" if result["enabled"] else "off"


def ensurepip_available(python=None) -> bool:
    try:
        probe = subprocess.run(
            [str(python or sys.executable), "-m", "ensurepip", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
        )
        return probe.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def create_venv(venv: Path, *, timeout) -> None:
    if ensurepip_available():
        run([sys.executable, "-m", "venv", str(venv)], "", timeout=timeout)
        return
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit(
            "python venv support is missing (ensurepip is unavailable) and uv "
            "was not found on PATH; install the python3-venv OS package "
            "(e.g. apt install python3.12-venv) or uv, then rerun setup"
        )
    run([uv, "venv", "--seed", str(venv)], "", timeout=timeout)


def build_bootstrap_runtime(domain_root: Path) -> str:
    venv = domain_root / "bootstrap-venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if python.exists():
        return str(python)
    env = dict(
        os.environ,
        PIP_RETRIES="0", PIP_NO_INPUT="1",
        PIP_DISABLE_PIP_VERSION_CHECK="1", GIT_TERMINAL_PROMPT="0",
    )
    create_venv(venv, timeout=180)
    run(
        [str(python), "-m", "pip", "install", "-r", str(PLUGIN_ROOT / "runtime-requirements.txt")],
        "", timeout=600, max_output=512 * 1024, env=env,
    )
    return str(python)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-python", default=None, help="operator override; default builds a pinned venv")
    parser.add_argument("--config", type=Path, default=default_config_path())
    parser.add_argument(
        "--root", type=Path,
        default=Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "mindie-agent",
    )
    parser.add_argument("--domain", default="vllm-ascend")
    parser.add_argument("--no-public-feed", action="store_true")
    parser.add_argument("--community-repository", metavar="OWNER/REPO")
    parser.add_argument("--community-project-root", action="append")
    parser.add_argument("--community-branch")
    parser.add_argument("--community-account")
    parser.add_argument("--community-fork")
    parser.add_argument("--community-visibility", choices=["public"])
    parser.add_argument("--kimi-home", type=Path, help="explicit Kimi home for native plugin install/update")
    parser.add_argument("--update-remote", default=None, help="git remote tracked for automatic updates")
    parser.add_argument("--no-schedule", action="store_true", help="do not register the automatic update check")
    args = parser.parse_args()
    if sys.version_info < (3, 11):
        parser.error("Python 3.11+ is required")
    community = community_settings(args, parser)
    config = args.config.expanduser().absolute()
    domain_root = args.root.expanduser().absolute() / "kimi"
    if args.knowledge_python:
        python = str(Path(args.knowledge_python).expanduser().absolute())
    else:
        python = build_bootstrap_runtime(domain_root)
    probe_runtime(python)
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.domain):
        parser.error("invalid domain name")
    engine_config = config.with_name(config.stem + ".engine.json")
    community_config = config.with_name("mindie-community.json")
    if config.exists() or engine_config.exists():
        if community is None:
            parser.error("configuration already exists; pass --community-* to configure sharing")
        sharing = write_community(
            community_config, community,
            consent_config=str(config.with_name("mindie-consent.json")), python=python,
        )
        print(json.dumps(dict(config=str(config), sharing=sharing, updated="community"), indent=2))
        return
    admission = domain_root / "admission.sqlite3"
    state_dir = domain_root / "adapter-state"
    bootstrap_root = stage_retained_bootstrap(state_dir / "update" / "generations" / "bootstrap", PLUGIN_ROOT)
    value = dict(
        root=str(domain_root),
        domain=args.domain,
        admission_path=str(admission),
        transcript_adapter=str(bootstrap_root / "scripts" / "transcript.py"),
        **__import__("capture_config").prepare(python, bootstrap_root / "scripts"),
        community_config=str(community_config),
    )
    if args.domain == "vllm-ascend" and not args.no_public_feed:
        value["feeds"] = [
            dict(
                repository="mindie-agent/knowledge-vllm-ascend",
                ref="main", domain="vllm-ascend", interval_seconds=300,
            )
        ]
    write_private(engine_config, value)
    kimi_home = (
        args.kimi_home.expanduser().absolute()
        if args.kimi_home
        else Path(os.environ.get("KIMI_CODE_HOME") or Path.home() / ".kimi-code").expanduser().absolute()
    )
    adapter_value = dict(
        python=python,
        engine_config=str(engine_config),
        community_config=str(community_config),
        state_dir=str(state_dir),
        profile_config=str(config),
        kimi_home=str(kimi_home),
    )
    if args.update_remote:
        adapter_value["update_remote"] = args.update_remote
    from paths import resolve_git_bin, resolve_kimi_bin

    try:
        kimi_bin = resolve_kimi_bin(adapter_value)
    except (OSError, ValueError):
        kimi_bin = None
    if kimi_bin is not None:
        adapter_value["kimi_bin"] = kimi_bin
    try:
        git_bin = resolve_git_bin()
    except (OSError, ValueError):
        git_bin = None
    if git_bin is not None:
        adapter_value["git_bin"] = git_bin
    write_private(config, adapter_value)
    sharing = write_community(
        community_config, community,
        consent_config=str(config.with_name("mindie-consent.json")), python=python,
    )
    import genstate
    import updater

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
            with contextlib.redirect_stdout(io.StringIO()):
                code = updater.install_schedule(config)
            scheduled = "registered" if code == 0 else "manual"
        except Exception as exc:
            scheduled = f"manual ({type(exc).__name__}: {str(exc)[:120]})"
    result = dict(
        config=str(config),
        engine_config=str(engine_config),
        community_config=str(community_config),
        admission_path=str(admission),
        native_package=str(bootstrap_package),
        domain=args.domain,
        sharing=sharing,
        update_schedule=scheduled,
        kimi_bin=kimi_bin,
        git_bin=git_bin,
        plugin_install=(
            "Native install: POST /api/v1/plugins {source: <host-package>} "
            "in the selected Kimi home (scripts/install_kimi_plugin.py). "
            "Automatic updates: scripts/updater.py check (scheduled), "
            "status via scripts/updater.py status, recovery via "
            "scripts/updater.py recover. See docs/update.md."
        ),
    )
    warnings = []
    if kimi_bin is None:
        warnings.append(
            "no kimi binary resolved (MINDIE_KIMI_BIN unset, no "
            "<kimi_home>/bin/kimi, not on PATH); the summary worker will "
            "report configuration-unavailable until one exists"
        )
    if git_bin is None:
        warnings.append(
            "no git executable resolved (MINDIE_KIMI_GIT_BIN unset, not on "
            "PATH); scheduled update checks will report configuration-"
            "unavailable until one exists"
        )
    if warnings:
        result["warnings"] = warnings
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
