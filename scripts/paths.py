"""[Role] Canonical path and binary resolution authority for the Kimi adapter.
[Invariants] Profile authority paths anchor at profile_config_path() (never per-generation forks); never mutates ~/.kimi.
[Invariants] domain_configs comes from the selected native generation; profile consent remains shared. Values stay absolute engine-config paths for core selection.
[Timeouts & Locks] Pure filesystem metadata and JSON config reads; zero locks or subprocesses.
[Side-Effect Policy] Strictly read-only path and configuration resolution.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
IDENTITY = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z"
NONCE = r"[A-Za-z0-9][A-Za-z0-9._-]{7,127}\Z"
PLUGIN_ID = "mindie-agent"
MCP_QUALIFIED_PREFIX = "mcp__plugin-mindie-agent_"
GIT_WINDOWS_LOCATIONS = (
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files\Git\bin\git.exe",
    r"C:\Program Files (x86)\Git\cmd\git.exe",
    r"C:\Program Files (x86)\Git\bin\git.exe",
    r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe",
    r"%LOCALAPPDATA%\Programs\Git\bin\git.exe",
    r"%USERPROFILE%\AppData\Local\Programs\Git\cmd\git.exe",
    r"%USERPROFILE%\scoop\apps\git\current\cmd\git.exe",
)
_WIN_SUFFIXES = frozenset({".exe", ".cmd", ".bat", ".ps1", ".com"})
_WSL_DRVFS_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")
_REAL_WHICH = shutil.which


def in_wsl() -> bool:
    """Return True when POSIX Python is running inside a WSL2 Linux environment."""
    if os.name != "posix":
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    except TimeoutError:
        raise
    except OSError:
        return False


def _has_pe_header(path: Path) -> bool:
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size < 64:
            return False
        head = os.read(fd, 64)
        if len(head) < 64 or head[:2] != b"MZ":
            return False
        pe_off = struct.unpack_from("<I", head, 0x3C)[0]
        if pe_off < 64 or pe_off + 4 > info.st_size or pe_off > 16 * 1024 * 1024:
            return False
        os.lseek(fd, pe_off, os.SEEK_SET)
        return os.read(fd, 4) == b"PE\x00\x00"
    except TimeoutError:
        raise
    except OSError:
        return False
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass


def is_windows_host_binary(candidate) -> bool:
    """Reject Windows PE/.exe/DrvFS shims when discovering POSIX/WSL host CLIs."""
    if not candidate or os.name != "posix":
        return False
    raw = os.fspath(candidate)
    if Path(raw).suffix.lower() in _WIN_SUFFIXES:
        return True
    norm = os.path.normpath(raw)
    if in_wsl() and _WSL_DRVFS_RE.match(norm):
        return True
    return _has_pe_header(Path(raw))


def is_windows_or_wsl_drvfs(path) -> bool:
    """Return True when POSIX mode bits (0o600) are not enforced by the filesystem."""
    if os.name == "nt":
        return True
    if not in_wsl():
        return False
    try:
        resolved = os.path.normpath(os.fspath(Path(path).resolve()))
    except TimeoutError:
        raise
    except OSError:
        resolved = os.path.normpath(os.fspath(path))
    return bool(_WSL_DRVFS_RE.match(resolved))


_WINAPPS_STUB_NAMES = frozenset({"python", "python.exe", "python3", "python3.exe", "bash", "bash.exe"})


def _is_windowsapps_stub(candidate) -> bool:
    if not candidate or os.name != "nt":
        return False
    norm = os.fspath(candidate).replace("/", "\\").lower()
    if "\\microsoft\\windowsapps\\" not in norm:
        return False
    if norm.rsplit("\\", 1)[-1] in _WINAPPS_STUB_NAMES:
        return True
    try:
        return os.stat(candidate).st_size == 0
    except TimeoutError:
        raise
    except OSError:
        return True


def which_host_binary(name: str, *, path: str | None = None, which_fn=None) -> str | None:
    """Resolve a host CLI without WSL2 `/mnt/<drive>` shims or WindowsApps alias stubs."""
    lookup = which_fn or shutil.which
    found = lookup(name) if path is None else lookup(name, path=path)
    if found is None:
        return None
    rejected = _is_windowsapps_stub if os.name == "nt" else (is_windows_host_binary if os.name == "posix" else None)
    if rejected is None or not rejected(found):
        return found
    search = os.environ.get("PATH", "") if path is None else path
    wsl = os.name == "posix" and in_wsl()
    for entry in search.split(os.pathsep):
        if not entry:
            continue
        if wsl and _WSL_DRVFS_RE.match(os.path.normpath(entry)):
            continue
        cand = _REAL_WHICH(name, path=entry)
        if cand and not rejected(cand):
            return cand
    return None


def _strip_win_extended(raw: str) -> str:
    if raw.startswith(("\\\\?\\UNC\\", "//?/UNC/")):
        return "\\\\" + raw[8:]
    if raw.startswith(("\\\\?\\", "//?/")):
        return raw[4:]
    return raw


def same_interpreter(left, right) -> bool:
    """Compare two interpreter paths safely across Windows `\\\\?\\` and drive case."""
    if not left or not right:
        return False
    a = _strip_win_extended(os.fspath(left))
    b = _strip_win_extended(os.fspath(right))
    if os.name == "nt":
        import ntpath

        if not ntpath.isabs(a):
            a = _strip_win_extended(os.path.abspath(a))
        if not ntpath.isabs(b):
            b = _strip_win_extended(os.path.abspath(b))
        return ntpath.normcase(ntpath.normpath(a)) == ntpath.normcase(ntpath.normpath(b))
    a = _strip_win_extended(str(Path(a).absolute()))
    b = _strip_win_extended(str(Path(b).absolute()))
    return os.path.normpath(a) == os.path.normpath(b)


def _default_config_path() -> Path:
    return (Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent" / "kimi.json").absolute()


def config_path() -> Path:
    if override := os.environ.get("MINDIE_KIMI_CONFIG"):
        return Path(override).expanduser().absolute()
    return _default_config_path()


def _read_config_quiet(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None


def profile_config_path() -> Path:
    path = config_path()
    data = _read_config_quiet(path) if path.is_file() else None
    if data is not None:
        profile = data.get("profile_config")
        if isinstance(profile, str) and os.path.isabs(profile):
            return Path(profile)
        try:
            generations = state_dir(data) / "update" / "generations"
            path.relative_to(generations)
            return _default_config_path()
        except TimeoutError:
            raise
        except (OSError, ValueError, KeyError, FileNotFoundError):
            pass
    return path


_DOMAIN_REF = re.compile(r"mindie://(?P<domain>[a-z][a-z0-9-]{0,63})/")


def domain_from_ref(ref):
    """Return the canonical authority domain of a mindie reference, if any."""
    match = _DOMAIN_REF.match(ref) if isinstance(ref, str) else None
    return match.group("domain") if match else None


def domain_configs(config=None) -> dict | None:
    """Trusted domain map of the generation selected by the native launcher."""
    if config is None:
        config = load_adapter_config()
    value = config.get("domain_configs")
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("adapter configuration domain_configs must be a mapping")
    return value


def load_adapter_config() -> dict:
    path = config_path()
    if not path.is_file():
        raise FileNotFoundError("MindIE Kimi adapter configuration is missing")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("adapter configuration must be one JSON object")
    return data


def engine_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("engine_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute engine_config")
    return Path(value)


def load_engine_config_at(path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or not {"root", "domain"} <= set(data):
        raise ValueError("engine configuration requires root and domain")
    if "session_activation" in data:
        raise ValueError("session_activation was removed; use admission_path")
    return data


def load_engine_config(config=None) -> dict:
    return load_engine_config_at(engine_config_path(config))


def admission_path(engine=None) -> Path:
    engine = engine if engine is not None else load_engine_config()
    value = engine.get("admission_path")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("engine configuration lacks an absolute admission_path")
    path = Path(value)
    if path.suffix.lower() in {".json", ".toml", ".yaml", ".yml", ".ini", ".cfg"}:
        raise ValueError("admission_path must be a SQLite file, not an adapter config")
    return path


def community_config_path(config=None) -> Path:
    config = config if config is not None else load_adapter_config()
    value = config.get("community_config")
    if not isinstance(value, str) or not os.path.isabs(value):
        raise ValueError("adapter configuration lacks an absolute community_config")
    return Path(value)


def default_state_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "mindie-agent" / "state"


def state_dir(config=None) -> Path:
    try:
        config = config if config is not None else load_adapter_config()
    except FileNotFoundError:
        return default_state_dir()
    value = config.get("state_dir")
    if isinstance(value, str) and os.path.isabs(value):
        return Path(value)
    return Path(load_engine_config(config)["root"]) / "kimi-adapter"


def first_use_path() -> Path:
    return state_dir() / "kimi.first-use.json"


def kimi_home_from_env() -> Path | None:
    value = os.environ.get("KIMI_CODE_HOME")
    return Path(value).expanduser() if isinstance(value, str) and value.strip() else None


def plugin_root_from_env() -> Path:
    value = os.environ.get("KIMI_PLUGIN_ROOT")
    return Path(value) if isinstance(value, str) and value.strip() else PLUGIN_ROOT


def configured_python(config=None) -> str:
    config = config if config is not None else load_adapter_config()
    python = config.get("python")
    if not isinstance(python, str) or not python:
        raise ValueError("adapter configuration has no runtime interpreter")
    return python


def resolve_kimi_bin(config=None):
    if explicit := os.environ.get("MINDIE_KIMI_BIN"):
        return explicit
    config = config if config is not None else load_adapter_config()
    stamped = config.get("kimi_bin")
    if isinstance(stamped, str) and stamped:
        return stamped
    home = config.get("kimi_home") or os.environ.get("KIMI_CODE_HOME")
    if isinstance(home, str) and home:
        candidate = Path(home) / "bin" / ("kimi.exe" if os.name == "nt" else "kimi")
        if candidate.is_file() and not is_windows_host_binary(candidate):
            return str(candidate)
    return which_host_binary("kimi", which_fn=shutil.which)


def _expand_git_location(raw: str) -> str:
    expanded = re.sub(r"%([^%]+)%", lambda m: os.environ.get(m.group(1), m.group(0)), raw)
    if expanded != raw and os.path.sep == "/":
        expanded = expanded.replace("\\", "/")
    return expanded


def resolve_git_bin():
    if explicit := os.environ.get("MINDIE_KIMI_GIT_BIN"):
        return explicit
    if found := which_host_binary("git", which_fn=shutil.which):
        return found
    if os.name == "nt":
        for raw in GIT_WINDOWS_LOCATIONS:
            candidate = _expand_git_location(raw)
            if "%" not in candidate and Path(candidate).is_file():
                return candidate
    return None
