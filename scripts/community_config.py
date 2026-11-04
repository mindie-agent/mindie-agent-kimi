"""[Role] Subprocess bridge and CLI handler for Kimi community sharing and consent configuration.
[Invariants] Executes CommunityWriteContext.configure inside the target runtime interpreter so schema validation and locking stay canonical; `sharing --status` is pure-stdlib and strictly read-only.
[Timeouts & Locks] Subprocess execution bounded at 30s (`configure`) and 15s (`gh` probes); acquires CommunityWriteContext file lock inside the child.
[Side-Effect Policy] `sharing --status` never mutates files or bootstraps a venv; `sharing --enable` validates all inputs before writing `mindie-community.json`, `mindie-consent.json`, and `diagnostics.json`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

SCRIPT = """
import json, sys
from mindie_knowledge.loop.settings import CommunityWriteContext
request = json.load(sys.stdin)
with CommunityWriteContext(request['path']) as context:
    result = context.configure(request['path'], request['settings'])
print(json.dumps(result.raw))
"""

DEFAULT_REPOSITORY = "mindie-agent/knowledge-vllm-ascend"
_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,120}$")
_ACCOUNT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,38}$")
_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
_MAX_BYTES = 64 * 1024


def configure(path, settings, python=None):
    completed = subprocess.run(
        [str(python or sys.executable), "-c", SCRIPT],
        input=json.dumps({"path": str(path), "settings": settings}).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise SystemExit("shared community configuration failed; nothing was written; existing bytes preserved")
    return json.loads(completed.stdout)


def _read_small_json(path: Path) -> tuple[str, dict]:
    if not path.is_symlink() and not path.exists():
        return "missing", {}
    try:
        if not path.is_file():
            return "unreadable", {}
        raw = path.read_bytes()
    except TimeoutError:
        raise
    except OSError:
        return "unreadable", {}
    if len(raw) > _MAX_BYTES:
        return "corrupt", {}
    try:
        data = json.loads(raw)
    except ValueError:
        return "corrupt", {}
    if not isinstance(data, dict):
        return "corrupt", {}
    return "ok", data


def _read_consent_stdlib(path: Path) -> tuple[str, str | None, str | None]:
    state, data = _read_small_json(path)
    if state != "ok":
        return state, None, None
    if data.get("schema") != "mindie-consent/1":
        return "corrupt", None, None
    choice = data.get("choice")
    if choice is not None and choice not in {"contribute", "read-only", "later", "disabled"}:
        return "corrupt", None, None
    reporting = data.get("reporting")
    if reporting is not None and reporting not in {"enabled", "disabled", "later"}:
        return "corrupt", None, None
    revision = data.get("choice_revision")
    if revision is not None and (not isinstance(revision, str) or not _HEX32_RE.fullmatch(revision)):
        return "corrupt", None, None
    return "ok", choice, revision


def _read_community_stdlib(path: Path) -> tuple[str, dict]:
    state, data = _read_small_json(path)
    if state != "ok":
        return state, {}
    if data.get("schema") != "mindie-community-config/1" or type(data.get("enabled")) is not bool:
        return "corrupt", {}
    return ("enabled" if data["enabled"] else "disabled"), data


def sharing_status(config: Path) -> dict:
    config = Path(config).expanduser().absolute()
    consent_file = config.with_name("mindie-consent.json")
    community_file = config.with_name("mindie-community.json")
    diag_override = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    diag_file = Path(diag_override).expanduser().absolute() if diag_override else config.with_name("diagnostics.json")

    consent_state, consent_choice, choice_revision = _read_consent_stdlib(consent_file)
    comm_state, comm_data = _read_community_stdlib(community_file)
    diag_state, diag_data = _read_small_json(diag_file)

    sharing_enabled = bool(
        comm_state == "enabled"
        and consent_state == "ok"
        and consent_choice == "contribute"
    )
    reporting_enabled = bool(
        diag_state == "ok"
        and (
            diag_data.get("enabled") is True
            or diag_data.get("decision") == "enabled"
            or diag_data.get("reporting") is True
        )
        and consent_state == "ok"
        and consent_choice == "contribute"
        and choice_revision
    )
    repair_required = consent_state in {"corrupt", "unreadable"} or comm_state in {"corrupt", "unreadable"}
    needs_onboarding_prompt = (not repair_required) and (
        consent_state == "missing"
        or (
            consent_state == "ok"
            and (
                consent_choice not in {"contribute", "disabled", "read-only", "later"}
                or (consent_choice == "contribute" and not choice_revision)
            )
        )
    )
    raw_roots = comm_data.get("project_roots")
    project_roots = [str(item) for item in raw_roots if isinstance(item, str)] if isinstance(raw_roots, list) else []
    return {
        "schema": "mindie-sharing-status/1",
        "harness": "kimi-code",
        "consent_state": consent_state,
        "consent_choice": consent_choice,
        "choice_revision": choice_revision,
        "sharing_enabled": sharing_enabled,
        "community_enabled": sharing_enabled,
        "reporting_enabled": reporting_enabled,
        "repository": comm_data.get("repository") or DEFAULT_REPOSITORY,
        "fork": comm_data.get("fork"),
        "account": comm_data.get("account"),
        "project_roots": project_roots,
        "needs_onboarding_prompt": needs_onboarding_prompt,
        "repair_required": repair_required,
    }


def _gh_quiet(*args: str, timeout: int = 15) -> str | None:
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        completed = subprocess.run(
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except TimeoutError:
        raise
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _resolve_gh_account_and_fork(repository: str, account: str | None, fork: str | None) -> tuple[str | None, str | None]:
    if fork and not account:
        account = fork.split("/", 1)[0]
    if not shutil.which("gh"):
        return account, fork
    if not account:
        login = _gh_quiet("api", "--hostname", "github.com", "user", "--jq", ".login")
        if login and "/" not in login and _ACCOUNT_RE.fullmatch(login):
            account = login
    if not fork and account:
        repo_name = repository.split("/", 1)[1]
        direct = _gh_quiet("api", "--hostname", "github.com", f"repos/{account}/{repo_name}")
        if direct:
            try:
                info = json.loads(direct)
                parent = (info.get("parent") or {}).get("full_name") if isinstance(info, dict) else None
                full_name = info.get("full_name") if isinstance(info, dict) else None
                if info.get("fork") is True and parent == repository and isinstance(full_name, str):
                    fork = full_name
            except ValueError:
                pass
        if not fork:
            listed = _gh_quiet(
                "repo", "list", account, "--fork", "--limit", "200", "--json", "nameWithOwner,parent"
            )
            if listed:
                try:
                    items = json.loads(listed)
                    if isinstance(items, list):
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            parent = item.get("parent")
                            parent_name = parent.get("nameWithOwner") if isinstance(parent, dict) else None
                            name = item.get("nameWithOwner")
                            if parent_name == repository and isinstance(name, str) and _REPO_RE.fullmatch(name):
                                fork = name
                                break
                except ValueError:
                    pass
        if not fork:
            _gh_quiet("repo", "fork", repository, "--clone=false", "--default-branch-only", timeout=30)
            after_fork = _gh_quiet("api", "--hostname", "github.com", f"repos/{account}/{repo_name}")
            if after_fork:
                try:
                    info = json.loads(after_fork)
                    parent = (info.get("parent") or {}).get("full_name") if isinstance(info, dict) else None
                    full_name = info.get("full_name") if isinstance(info, dict) else None
                    if info.get("fork") is True and parent == repository and isinstance(full_name, str):
                        fork = full_name
                except ValueError:
                    pass
    return account, fork


def sharing_parser(default_config: Path) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="setup.py sharing",
        description="Inspect or configure MindIE community sharing and fault reporting.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="print read-only sharing and consent status")
    group.add_argument("--enable", action="store_true", help="enable community sharing and fault reporting")
    group.add_argument("--disable", action="store_true", help="disable community sharing and fault reporting")
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--project-root", action="append", default=None)
    parser.add_argument("--fork", default=None)
    parser.add_argument("--account", default=None)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--visibility", default="public")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--knowledge-python", default=None)
    return parser


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_bytes((json.dumps(data, indent=2) + "\n").encode("utf-8"))
    os.replace(tmp, path)


def _apply_stdlib_sharing(
    config: Path,
    community_file: Path,
    consent_file: Path,
    request: dict,
    choice: str,
) -> dict:
    comm_doc = dict(request)
    comm_doc.setdefault("schema", "mindie-community-config/1")
    comm_doc.setdefault("generation", secrets.token_hex(16))
    comm_doc.setdefault("enabled_at", time.time())
    comm_doc.setdefault("visibility", "public")
    _atomic_write_json(community_file, comm_doc)
    consent_doc = {
        "schema": "mindie-consent/1",
        "choice": choice,
        "choice_at": time.time(),
        "choice_revision": secrets.token_hex(16),
    }
    _atomic_write_json(consent_file, consent_doc)
    diag_override = os.environ.get("MINDIE_DIAGNOSTICS_CONFIG")
    diag_file = Path(diag_override).expanduser().absolute() if diag_override else config.with_name("diagnostics.json")
    enabled = choice == "contribute"
    _atomic_write_json(
        diag_file,
        {
            "schema": "mindie-diagnostics-config/1",
            "enabled": enabled,
            "reporting": enabled,
            "consent_config": str(consent_file.resolve()),
        },
    )
    return {"status": "configured" if enabled else "disabled", "enabled": enabled}


def run_sharing_cli(
    argv: list[str],
    *,
    default_config: Path,
    resolve_python,
    activate_reporting_fn,
) -> int:
    parser = sharing_parser(default_config)
    args = parser.parse_args(argv)
    config = args.config.expanduser().absolute()
    current = sharing_status(config)
    if args.status:
        print(json.dumps(current, indent=2))
        return 0
    if current["repair_required"]:
        raise SystemExit("saved consent or community configuration is damaged; preserving existing bytes")

    consent_file = config.with_name("mindie-consent.json")
    community_file = config.with_name("mindie-community.json")

    if args.enable:
        repository = args.repository
        if not isinstance(repository, str) or not _REPO_RE.fullmatch(repository):
            parser.error("repository must look like owner/repo")
        if args.visibility != "public":
            parser.error("visibility must be public")
        branch = args.branch or "main"
        if not isinstance(branch, str) or not _BRANCH_RE.fullmatch(branch) or ".." in branch:
            parser.error("invalid branch name")
        account = args.account if args.account is not None else current.get("account")
        if account is not None and (not isinstance(account, str) or not _ACCOUNT_RE.fullmatch(account)):
            parser.error("account must be a valid GitHub username without '/'")
        fork = args.fork if args.fork is not None else current.get("fork")
        if fork is not None and (not isinstance(fork, str) or not _REPO_RE.fullmatch(fork)):
            parser.error("fork must look like owner/repo")
        if account and fork and fork.split("/", 1)[0] != account:
            parser.error("fork must belong to the specified account")

        raw_roots = args.project_root if args.project_root else (current["project_roots"] or [str(Path.cwd().resolve())])
        roots: list[str] = []
        for item in raw_roots:
            canonical = str(Path(item).expanduser().resolve())
            if not Path(canonical).is_dir():
                parser.error(f"community project root does not exist: {canonical}")
            if canonical not in roots:
                roots.append(canonical)

        account, fork = _resolve_gh_account_and_fork(repository, account, fork)
        if not fork:
            parser.error("could not resolve or create a personal GitHub fork; ensure `gh` is authenticated or pass --fork owner/repo")
        if account and fork and fork.split("/", 1)[0] != account:
            parser.error("fork must belong to the resolved account")

        python = resolve_python(config, args.knowledge_python)
        request = {
            "schema": "mindie-community-config/1",
            "enabled": True,
            "generation": secrets.token_hex(16),
            "enabled_at": time.time(),
            "repository": repository,
            "branch": branch,
            "project_roots": roots,
            "idle_seconds": 300,
            "visibility": "public",
            "consent_config": str(consent_file.resolve()),
        }
        if account:
            request["account"] = account
        if fork:
            request["fork"] = fork
        if python is not None:
            configure(community_file, request, python)
            reporting = activate_reporting_fn(python, config, contribution=True, choice="contribute")
        else:
            reporting = _apply_stdlib_sharing(config, community_file, consent_file, request, "contribute")
        updated = sharing_status(config)
        updated.update(status="enabled", sharing="enabled", reporting=reporting)
        print(json.dumps(updated, indent=2))
        return 0

    python = resolve_python(config, args.knowledge_python)
    request = {
        "enabled": False,
        "repository": current["repository"] if community_file.is_file() else None,
        "project_roots": current["project_roots"],
        "branch": "main",
        "idle_seconds": 300,
        "consent_config": str(consent_file.resolve()),
    }
    if current.get("account"):
        request["account"] = current["account"]
    if current.get("fork"):
        request["fork"] = current["fork"]
    if python is not None:
        reporting = activate_reporting_fn(python, config, contribution=False, choice="disabled")
        configure(community_file, request, python)
    else:
        reporting = _apply_stdlib_sharing(config, community_file, consent_file, request, "disabled")
    updated = sharing_status(config)
    updated.update(status="disabled", sharing="off", reporting=reporting)
    print(json.dumps(updated, indent=2))
    return 0
