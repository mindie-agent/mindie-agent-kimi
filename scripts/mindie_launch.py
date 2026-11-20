#!/usr/bin/env python3
"""[Role] Pure-stdlib cold-start launcher: resolves the Kimi profile and forwards `mcp`/`hook`/`updater`.
[Invariants] Zero third-party imports and zero SQLite on hook paths; `pretool` never reads stdin;
`stop` exits `{}` unless consent (`contribute`/`local-only`) and an absolute `project_roots` entry
authorize `cwd` via `os.path.commonpath`; `(python_exe, scripts_dir, config_path)` resolves from
one snapshot preferring `kimi.json` over stale `update/current.json`, and seed isolates `stdin`.
[Timeouts & Locks] Bounded `1 MiB + 1` stdin; zero hook locks; seed provisioning owns its lock.
[Side-Effect Policy] Writes `state/kimi/pending-update-check.json`, provisions the verified seed
runtime when needed, then execs/spawns `entry.py`; stdout stays JSON-only.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_NO_WIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
_SHIMS = {".exe", ".cmd", ".bat", ".com", ".ps1"}
_RAW_HERE = Path(__file__).resolve().parent
HERE = (
    _RAW_HERE.parents[1] / "generations" / _RAW_HERE.name / "scripts"
    if len(_RAW_HERE.parents) >= 2 and _RAW_HERE.parent.name == "launch"
    and (_RAW_HERE.parents[1] / "generations" / _RAW_HERE.name / "scripts" / "entry.py").is_file()
    else _RAW_HERE
)
PLUGIN_ROOT = HERE.parent
SEED_SCHEMA = "mindie-harness-seed/1"
MAX_STOP_BYTES = 1048577

def _config_home() -> Path:
    if explicit := os.environ.get("MINDIE_CONFIG_HOME"):
        return Path(explicit).expanduser()
    return Path(os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming") if os.name == "nt" else (os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config"))) / "mindie-agent"

def _data_home() -> Path:
    if explicit := os.environ.get("MINDIE_DATA_HOME"):
        return Path(explicit).expanduser()
    return Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local") if os.name == "nt" else (os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))) / "mindie-agent"

def _config_path() -> Path:
    raw = os.environ.get("MINDIE_KIMI_CONFIG") or os.environ.get("MINDIE_AGENT_CONFIG")
    return Path(raw).expanduser().absolute() if raw else (_config_home() / "kimi.json").absolute()

def _state_dir() -> Path:
    return _data_home() / "state" / "kimi"

def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_bytes().decode("utf-8-sig")) if path.is_file() else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None

def _valid_python(raw: str | Path | None, *, os_name: str = os.name) -> Path | None:
    if not isinstance(raw, (str, Path)) or not (text := str(raw).strip()):
        return None
    path = Path(text)
    try:
        if os_name == "nt":
            if "\\windowsapps\\" in text.replace("/", "\\").lower() or path.parent.name.lower() == "windowsapps":
                return None
            return path if path.is_file() and path.stat().st_size > 0 else None
        return path if path.suffix.lower() not in _SHIMS and path.is_file() and (os.name == "nt" or os.access(path, os.X_OK)) else None
    except TimeoutError:
        raise
    except OSError:
        return None

def _discover_python(*, path_env: str | None = None, os_name: str = os.name, allow_uv: bool = True) -> Path | None:
    raw_path = os.environ.get("PATH", "") if path_env is None else path_env
    names = ("python.exe", "python3.exe") if os_name == "nt" else ("python3", "python")
    uv_names, uv_bin = (("uv.exe", "uv.cmd", "uv") if os_name == "nt" else ("uv",)), None
    for folder in raw_path.split(os.pathsep):
        if not (cleaned := folder.strip().strip('"')):
            continue
        base = Path(cleaned)
        for name in names:
            if (accepted := _valid_python(base / name, os_name=os_name)) is not None:
                return accepted
        if uv_bin is None and allow_uv:
            uv_bin = next((p for n in uv_names if (p := _valid_python(base / n, os_name=os_name)) is not None), None)
    if allow_uv and uv_bin is not None:
        try:
            probe = subprocess.run([str(uv_bin), "python", "find", ">=3.11"], stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace", timeout=5.0, check=False, creationflags=_NO_WIN)
            return _valid_python(probe.stdout.strip(), os_name=os_name) if probe.returncode == 0 else None
        except TimeoutError:
            raise
        except OSError:
            return None
    return None

def _sharing_roots(cfg_path: Path) -> list[str]:
    consent, community_path = _read_json(cfg_path.parent / "mindie-consent.json") or {}, cfg_path.parent / "mindie-community.json"
    community, choice = _read_json(community_path), consent.get("choice")
    if choice not in {"contribute", "local-only"} and not (choice is None and consent.get("enabled") is True):
        return []
    if community_path.is_file() and (not isinstance(community, dict) or community.get("enabled") is not True):
        return []
    roots = community.get("project_roots") if community_path.is_file() and isinstance(community, dict) else consent.get("project_roots")
    if not isinstance(roots, list) or not roots or any(not isinstance(item, str) or not os.path.isabs(item) for item in roots):
        return []
    return [os.path.normcase(str(Path(item).resolve())) for item in roots]

def _in_roots(raw: bytes, roots: list[str]) -> bool:
    if not raw.strip() or len(raw) > MAX_STOP_BYTES - 1:
        return False
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return False
    if not isinstance(event, dict):
        return False
    extra = event.get("workspacePaths")
    items = [event["cwd"]] if isinstance(event.get("cwd"), str) and os.path.isabs(event["cwd"]) else [event.get(k) for k in ("cwd", "workspace", "project_root")] + (extra if isinstance(extra, list) else [])
    for item in items:
        if isinstance(item, str) and os.path.isabs(item):
            candidate = os.path.normcase(str(Path(item).resolve()))
            for root in roots:
                if os.path.splitdrive(candidate)[0] == os.path.splitdrive(root)[0]:
                    try:
                        if os.path.commonpath([candidate, root]) == root:
                            return True
                    except TimeoutError:
                        raise
                    except (OSError, ValueError):
                        continue
    return False

def _legacy_current_tuple(cfg_path: Path, cfg: dict | None = None) -> tuple[Path, Path, Path] | None:
    cfg = _read_json(cfg_path) or {} if cfg is None else cfg
    if cfg.get("source_root") == str(PLUGIN_ROOT) and _RAW_HERE.parent.name != "launch":
        return None
    candidates: list[Path] = [_RAW_HERE.parents[1] / "current.json"] if _RAW_HERE.parent.name == "launch" and len(_RAW_HERE.parents) >= 2 else []
    if isinstance(sd := cfg.get("state_dir"), str) and os.path.isabs(sd):
        candidates.append(Path(sd) / "update" / "current.json")
    if isinstance(ep := cfg.get("engine_config"), str) and os.path.isabs(ep) and isinstance(root := (_read_json(Path(ep)) or {}).get("root"), str) and os.path.isabs(root):
        candidates.append(Path(root) / "kimi-adapter" / "update" / "current.json")
    candidates.extend([_state_dir() / "update" / "current.json", cfg_path.parent / "state" / "update" / "current.json"])
    for cur_path in candidates:
        cur = _read_json(cur_path) or {}
        py_raw, gen_raw, ad_raw = cur.get("python"), cur.get("generation"), cur.get("adapter_config")
        if isinstance(py_raw, str) and isinstance(gen_raw, str) and isinstance(ad_raw, str):
            scripts = Path(gen_raw) / "scripts"
            if (py := _valid_python(py_raw)) is not None and (scripts / "entry.py").is_file() and Path(ad_raw).is_file():
                return py, scripts, Path(ad_raw)
    return None

def _resolve_target(cfg_path: Path) -> tuple[Path | None, Path, Path]:
    cfg = _read_json(cfg_path) or {}
    rs = cfg.get("runtime_scripts")
    cfg_scripts = Path(rs) if isinstance(rs, str) and (Path(rs) / "entry.py").is_file() else HERE
    if isinstance(release := cfg.get("component_release"), str) and release:
        receipt = _read_json(Path(release) / "runtime.json") or {}
        if (receipt.get("validation") or {}).get("status") == "validated" and (py := _valid_python(receipt.get("python") or cfg.get("python"))) is not None:
            return py, cfg_scripts, cfg_path
    if (py := _valid_python(cfg.get("python"))) is not None:
        return py, cfg_scripts, cfg_path
    if (tup := _legacy_current_tuple(cfg_path, cfg)) is not None:
        return tup[0], tup[1], (tup[2] if (tup[1] / "mcp_server.py").is_file() or not cfg_path.is_file() else cfg_path)
    return None, HERE, cfg_path

def _effective_scripts(cfg_path: Path) -> Path:
    return _resolve_target(cfg_path)[1]

def _active_python(cfg_path: Path) -> Path | None:
    return _resolve_target(cfg_path)[0]

def _has_harness() -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec("mindie_harness") is not None
    except (ImportError, ValueError):
        return False

def _verify_seed() -> None:
    assets = PLUGIN_ROOT / "assets"
    lock, pyz = _read_json(assets / "seed-lock.json") or {}, assets / "mindie-harness-seed.pyz"
    if lock.get("schema") != SEED_SCHEMA or not pyz.is_file():
        raise SystemExit("invalid bundled mindie-harness seed")
    digest, size = hashlib.sha256(), 0
    with pyz.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk); size += len(chunk)
    if size != lock.get("size_bytes") or digest.hexdigest() != lock.get("sha256"):
        raise SystemExit("mindie-harness-seed.pyz SHA-256 mismatch")

def _provision(cfg_path: Path) -> tuple[Path | None, Path, Path]:
    _verify_seed()
    if (seed_py := _valid_python(sys.executable) or _discover_python()) is None:
        return None, HERE, cfg_path
    res = subprocess.run([str(seed_py), "-I", "-X", "utf8", str(PLUGIN_ROOT / "assets" / "mindie-harness-seed.pyz"), "--host", "kimi", "--plugin-root", str(PLUGIN_ROOT), "--config", str(cfg_path)], stdin=subprocess.DEVNULL, capture_output=True, creationflags=_NO_WIN)
    return _resolve_target(cfg_path) if res.returncode == 0 else (None, HERE, cfg_path)

def _enqueue(state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = state_dir / f".pending-update-check.{os.getpid()}.tmp"
    try:
        tmp.write_text(json.dumps({"schema": "mindie-pending-update/1", "host": "kimi", "reason": "session_start", "requested_at": time.time()}, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, state_dir / "pending-update-check.json")
    finally:
        tmp.unlink(missing_ok=True)

def _same_py(left: str | Path, right: str | Path) -> bool:
    a, b = Path(left), Path(right)
    return os.path.normcase(str(a.parent.resolve() / a.name)) == os.path.normcase(str(b.parent.resolve() / b.name))

def _dispatch(python: Path, cfg_path: Path, forwarded: list[str], stdin_buf: bytes | None, *, scripts: Path | None = None, timeout: float | None = None) -> int:
    eff_scripts = _effective_scripts(cfg_path) if scripts is None else scripts
    if stdin_buf is None and _same_py(python, sys.executable):
        sys.path.insert(0, str(eff_scripts))
        import entry
        return int(entry.main(["--config", str(cfg_path), *forwarded]) or 0)
    argv = [str(python), "-X", "utf8", str(eff_scripts / "entry.py"), "--config", str(cfg_path), *forwarded]
    if not _same_py(python, sys.executable):
        os.environ.pop("PYTHONPATH", None)
    if stdin_buf is None and os.name != "nt":
        os.execv(str(python), argv)
    if stdin_buf is not None or timeout is not None:
        return subprocess.run(argv, input=stdin_buf, capture_output=timeout is not None, timeout=timeout, creationflags=_NO_WIN).returncode
    try:
        sys.stdin.fileno(); sys.stdout.fileno(); sys.stderr.fileno()
        io_kw = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    except TimeoutError:
        raise
    except (OSError, ValueError):
        io_kw = {}
    return subprocess.run(argv, **io_kw, creationflags=_NO_WIN).returncode

def _empty() -> int:
    sys.stdout.buffer.write(b"{}\n"); sys.stdout.buffer.flush(); return 0

def main(argv: list[str] | None = None) -> int:
    args, cfg_arg = list(sys.argv[1:] if argv is None else argv), None
    while "--config" in args:
        idx = args.index("--config")
        if idx + 1 >= len(args):
            raise SystemExit("--config requires a path")
        cfg_arg, args = args[idx + 1], args[:idx] + args[idx + 2:]
    cfg_path = Path(cfg_arg).expanduser().absolute() if cfg_arg else _config_path()
    if len(args) < 2:
        raise SystemExit("usage: mindie_launch.py [--config PATH] mcp <surface> | hook <event>")
    family, operation = args[0], args[1].lower().replace("_", "-")
    if family == "mcp" and operation in {"knowledge", "remote", "components", "tools", "all"}:
        python, scripts, target_cfg = _resolve_target(cfg_path)
        if python is None:
            cur_py = _valid_python(sys.executable) if _has_harness() else None
            python, scripts, target_cfg = (cur_py, HERE, cfg_path) if cur_py is not None else _provision(cfg_path)
        return 1 if python is None else _dispatch(python, target_cfg, ["mcp", operation], None, scripts=scripts)
    if family == "hook" and operation in {"pretool", "session-start"}:
        if operation == "session-start":
            try:
                _enqueue(_state_dir())
            except TimeoutError:
                raise
            except OSError:
                pass
        return _empty()
    if family == "hook" and operation == "stop":
        if (roots := _sharing_roots(cfg_path)) and _in_roots(stdin_buf := sys.stdin.buffer.read(MAX_STOP_BYTES), roots):
            python, scripts, target_cfg = _resolve_target(cfg_path)
            if python is not None:
                try:
                    _dispatch(python, target_cfg, ["hook", "stop"], stdin_buf, scripts=scripts, timeout=8.0)
                except subprocess.TimeoutExpired:
                    pass
        return _empty()
    if family == "updater" and operation in {"check", "status"}:
        if operation == "status":
            sys.stdout.write('{"result": "current"}\n'); return 0
        python, scripts, target_cfg = _resolve_target(cfg_path)
        if python is None and (cur_py := _valid_python(sys.executable) if _has_harness() else None) is not None:
            python, scripts, target_cfg = cur_py, HERE, cfg_path
        if python is None:
            sys.stdout.write('{"result": "deferred"}\n'); return 0
        return subprocess.run([str(python), "-I", "-X", "utf8", str(scripts / "setup.py"), "--config", str(target_cfg), "update", "--background"], creationflags=_NO_WIN).returncode
    raise SystemExit(f"unsupported operation: {family} {operation}")

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
