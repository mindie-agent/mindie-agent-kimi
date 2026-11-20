#!/usr/bin/env python3
"""[Role] Windowless native Kimi plugin installer and readback verifier over the
`kimi web` HTTP API (`POST/GET /api/v1/plugins`) in an explicit `--kimi-home`.
[Invariants] Never mutates `~/.kimi-code` implicitly or fabricates `plugins/installed.json`;
rejects records with skipped MCP servers (`knowledge`, `remote`, `components`) or hooks
(`PreToolUse`, `Stop`); native `kimi` and fallback Python scans skip Microsoft Store
`WindowsApps` stubs (`st_size == 0` / `\\windowsapps\\`) and WSL `.exe` / `MZ` shims.
[Timeouts & Locks] Readiness poll `20s` (`0.4s` interval), HTTP timeout `15s`, child
termination grace `3s` (`terminate` then `kill`).
[Side-Effect Policy] Starts a temporary local `kimi web` child in `--kimi-home`, posts
`/api/v1/plugins` when installing, and always terminates that child in `finally`.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_NO_WIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
EXPECTED_MCP = ("knowledge", "remote", "components")
_SHIMS = {".exe", ".cmd", ".bat", ".com", ".ps1"}

def _valid_candidate(candidate: Path, *, os_name: str = os.name) -> bool:
    try:
        if os_name == "nt":
            if "\\windowsapps\\" in str(candidate).replace("/", "\\").lower() or candidate.parent.name.lower() == "windowsapps":
                return False
            return candidate.is_file() and candidate.stat().st_size > 0
        if candidate.suffix.lower() in _SHIMS or not candidate.is_file() or (os.name != "nt" and not os.access(candidate, os.X_OK)):
            return False
        with candidate.open("rb") as stream:
            return stream.read(2) != b"MZ"
    except TimeoutError:
        raise
    except OSError:
        return False

def _scan_path(names: tuple[str, ...], *, path_env: str | None = None, os_name: str = os.name) -> str | None:
    raw = os.environ.get("PATH", "") if path_env is None else path_env
    for folder in raw.split(os.pathsep):
        if not (cleaned := folder.strip().strip('"')):
            continue
        base = Path(cleaned)
        for name in names:
            if _valid_candidate(base / name, os_name=os_name):
                return str(base / name)
    return None

def _which_kimi(os_name: str = os.name) -> str | None:
    if explicit := os.environ.get("MINDIE_KIMI_BIN"):
        return explicit
    exts = tuple(dict.fromkeys(["", *(e.lower() for e in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";") if e)] if os_name == "nt" else [""]))
    return _scan_path(tuple(f"kimi{ext}" for ext in exts), os_name=os_name)

def _find_python(path_env: str | None = None, *, os_name: str = os.name, allow_uv: bool = True) -> str | None:
    names = ("python.exe", "python3.exe") if os_name == "nt" else ("python3", "python")
    if found := _scan_path(names, path_env=path_env, os_name=os_name):
        return found
    uv_bin = _scan_path(("uv.exe", "uv.cmd", "uv") if os_name == "nt" else ("uv",), path_env=path_env, os_name=os_name) if allow_uv else None
    if uv_bin is not None:
        try:
            probe = subprocess.run(
                [uv_bin, "python", "find", ">=3.11"], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, errors="replace", timeout=5.0, check=False, creationflags=_NO_WIN,
            )
            if probe.returncode == 0 and (cand := probe.stdout.strip()) and _valid_candidate(Path(cand), os_name=os_name):
                return cand
        except TimeoutError:
            raise
        except OSError:
            return None
    return None

def expected_native_resources(package: Path, manifest: dict) -> dict:
    servers = list((manifest.get("mcpServers") or {}).keys())
    names = [name for name in EXPECTED_MCP if name in servers] or servers
    return {"mcp": names, "mcp_count": len(names), "hooks": len(manifest.get("hooks") or [])}

def native_record_resource_problems(record: dict, expected: dict, install_data: dict | None = None) -> list[str]:
    problems: list[str] = []
    count = expected["mcp_count"]
    if record.get("mcpServerCount") != count:
        problems.append(f"mcpServerCount={record.get('mcpServerCount')!r}")
    if record.get("enabledMcpServerCount") != count:
        problems.append(f"enabledMcpServerCount={record.get('enabledMcpServerCount')!r}")
    if expected["hooks"] and record.get("hookCount") != expected["hooks"]:
        problems.append(f"hookCount={record.get('hookCount')!r}")
    if not isinstance(install_data, dict):
        return problems
    servers = install_data.get("mcpServers")
    if count and not isinstance(servers, list):
        problems.append("install mcpServers missing or empty")
    elif isinstance(servers, list):
        names = {item.get("name") for item in servers if isinstance(item, dict)}
        if missing := [name for name in expected["mcp"] if name not in names]:
            problems.append(f"install mcpServers missing {missing}")
        if disabled := [item.get("name") for item in servers if isinstance(item, dict)
                        and item.get("enabled") is False and item.get("name") in expected["mcp"]]:
            problems.append(f"install mcpServers disabled {disabled}")
    for item in install_data.get("diagnostics") or []:
        message = str(item.get("message") or "") if isinstance(item, dict) else ""
        if message and ("skipped" in message.lower() or any(f"mcpServers.{name}" in message for name in expected["mcp"])):
            problems.append("diagnostics skipped expected resources: " + message)
    return problems

def verify_install_report(report: dict, package: Path, manifest: dict) -> None:
    data = ((report.get("install") or {}).get("body") or {}).get("data") or {}
    name = manifest.get("name", "mindie-agent")
    if data.get("id") != name or data.get("hasErrors"):
        raise SystemExit("native install rejected: " + json.dumps(report, sort_keys=True)[:1500])
    expected = expected_native_resources(package, manifest)
    problems = native_record_resource_problems(data, expected, data)
    plugins = (((report.get("after") or {}).get("body") or {}).get("data") or {}).get("plugins")
    records = [item for item in plugins if isinstance(item, dict) and item.get("id") == name] if isinstance(plugins, list) else []
    if len(records) != 1:
        problems.append(f"after inventory holds {len(records)} records")
    else:
        record = records[0]
        for key, want in (("version", manifest.get("version")), ("enabled", True), ("state", "ok")):
            if record.get(key) != want:
                problems.append(f"{key}={record.get(key)!r}")
        if record.get("hasErrors"):
            problems.append("after inventory hasErrors")
        problems.extend(native_record_resource_problems(record, expected))
    if problems:
        raise SystemExit("native install did not enable expected MCP/hook resources: " + "; ".join(problems))

def call(port: int, token: str, method: str, path: str, data=None, *, timeout: float = 15.0) -> dict:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if data is None else json.dumps(data).encode(),
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            return {"http": response.status, "body": json.load(response)}
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, "body": json.loads(exc.read())}

def wait_ready(port: int, token_path: Path, process: subprocess.Popen, deadline: float) -> tuple[str | None, dict | None]:
    while time.monotonic() < deadline and process.poll() is None:
        if token_path.exists():
            try:
                token = token_path.read_text(encoding="utf-8").strip()
                remaining = max(0.2, min(2.0, deadline - time.monotonic()))
                resp = call(port, token, "GET", "/api/v1/plugins", timeout=remaining)
                if resp.get("http") == 200:
                    return token, resp
            except TimeoutError:
                raise
            except (OSError, ValueError):
                pass
        time.sleep(0.4)
    return None, None

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kimi-home", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--readback", action="store_true", help="only GET the native plugin registry, no install")
    args = parser.parse_args(argv)
    home, source = args.kimi_home.expanduser().resolve(), args.plugin_root.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    if not (home / "config.toml").exists():
        (home / "config.toml").write_text("builtin_product_skills = false\n", encoding="utf-8")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    kimi = _which_kimi()
    if kimi is None:
        raise SystemExit("kimi binary is not installed (set MINDIE_KIMI_BIN or add it to PATH)")
    process: subprocess.Popen | None = None
    report: dict = {}
    try:
        process = subprocess.Popen(
            [kimi, "web", "--no-open", "--port", str(port)], cwd=str(home),
            env={**os.environ, "KIMI_CODE_HOME": str(home)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WIN,
        )
        token, report["before"] = wait_ready(port, home / "server.token", process, time.monotonic() + 20.0)
        if token is None:
            raise SystemExit("native Kimi plugin API did not become ready")
        if not args.readback:
            report["install"] = call(port, token, "POST", "/api/v1/plugins", {"source": str(source)})
        report["after"] = call(port, token, "GET", "/api/v1/plugins")
    finally:
        if process is not None:
            try:
                process.terminate()
            except TimeoutError:
                raise
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
    if (report.get("after") or {}).get("http") != 200:
        raise SystemExit("native plugin inventory readback failed: " + json.dumps(report, sort_keys=True)[:1500])
    if not args.readback:
        verify_install_report(report, source, json.loads((source / "kimi.plugin.json").read_text(encoding="utf-8-sig")))
    print(json.dumps(report, indent=2))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokenPipeError:
        raise SystemExit(0)
