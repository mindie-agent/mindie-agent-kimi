#!/usr/bin/env python3
"""Native Kimi plugin installer and readback verifier.

[Role]
Installs or inspects this plugin through Kimi's native HTTP plugin API
(`kimi web --no-open`) inside an explicit `--kimi-home` directory and verifies
that expected MCP servers, hooks, and slash commands are enabled.

[Invariants]
- Never fabricates `plugins/installed.json` directly or deletes a managed root.
- Requires explicit `--kimi-home` so `~/.kimi-code` is never mutated implicitly.
- Rejects native records where MCP servers, hooks, or commands were skipped.

[Timeouts & Locks]
- Server readiness poll: 20s (`0.4s` interval); HTTP request timeout: 15s;
  child termination grace: 3s (`terminate` then `kill`).

[Side-Effect Policy]
- Starts a temporary local `kimi web` child process in `--kimi-home`, posts
  `/api/v1/plugins` when installing, and always terminates the child process.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
KIMI = os.environ.get("MINDIE_KIMI_BIN") or "kimi"
EXPECTED_MCP_SURFACES = ("knowledge", "remote", "components")


def _command_files(package: Path) -> int:
    commands = package / "commands"
    return sum(1 for p in commands.rglob("*.md") if p.is_file()) if commands.is_dir() else 0


def expected_native_resources(package: Path, manifest: dict) -> dict:
    servers = manifest.get("mcpServers") or {}
    names = [name for name in EXPECTED_MCP_SURFACES if name in servers]
    if set(servers) != set(names):
        names = list(servers)
    return {
        "mcp": names,
        "mcp_count": len(names),
        "hooks": len(manifest.get("hooks") or []),
        "commands": _command_files(package),
    }


def _diagnostics_skipped_expected(diagnostics, expected: dict) -> list:
    if not isinstance(diagnostics, list):
        return []
    problems = []
    for item in diagnostics:
        message = str(item.get("message") or "") if isinstance(item, dict) else ""
        if not message:
            continue
        if any(f"mcpServers.{name}" in message for name in expected["mcp"]) or (
            "skipped" in message.lower() or "must be a path command" in message.lower()
        ):
            problems.append(message)
    return problems


def native_record_resource_problems(record: dict, expected: dict, install_data: dict | None = None) -> list:
    problems = []
    mcp_n = expected["mcp_count"]
    if record.get("mcpServerCount") != mcp_n:
        problems.append(f"mcpServerCount={record.get('mcpServerCount')!r}")
    if record.get("enabledMcpServerCount") != mcp_n:
        problems.append(f"enabledMcpServerCount={record.get('enabledMcpServerCount')!r}")
    if expected["hooks"] and record.get("hookCount") != expected["hooks"]:
        problems.append(f"hookCount={record.get('hookCount')!r}")
    if expected["commands"] and record.get("commandCount") != expected["commands"]:
        problems.append(f"commandCount={record.get('commandCount')!r}")
    if isinstance(install_data, dict):
        servers = install_data.get("mcpServers")
        if mcp_n:
            if not isinstance(servers, list) or not servers:
                problems.append("install mcpServers missing or empty")
            else:
                names = {item.get("name") for item in servers if isinstance(item, dict)}
                if missing := [name for name in expected["mcp"] if name not in names]:
                    problems.append(f"install mcpServers missing {missing}")
                if disabled := [
                    item.get("name") for item in servers
                    if isinstance(item, dict) and item.get("enabled") is False and item.get("name") in expected["mcp"]
                ]:
                    problems.append(f"install mcpServers disabled {disabled}")
        if skipped := _diagnostics_skipped_expected(install_data.get("diagnostics") or [], expected):
            problems.append("diagnostics skipped expected resources: " + "; ".join(skipped[:4]))
    return problems


def native_after_inventory_problems(report: dict, package: Path, manifest: dict, expected: dict) -> list:
    plugins = (((report.get("after") or {}).get("body") or {}).get("data") or {}).get("plugins")
    if not isinstance(plugins, list):
        return ["after inventory missing or not a list"]
    ident = manifest.get("name", "mindie-agent")
    records = [p for p in plugins if isinstance(p, dict) and p.get("id") == ident]
    if len(records) != 1:
        return [f"after inventory holds {len(records)} records"]
    record = records[0]
    problems = []
    if record.get("version") != manifest.get("version"):
        problems.append(f"version={record.get('version')!r}")
    if record.get("enabled") is not True:
        problems.append(f"enabled={record.get('enabled')!r}")
    if record.get("state") != "ok" or record.get("hasErrors"):
        problems.append(f"state={record.get('state')!r}")
    if record.get("originalSource") != str(package):
        problems.append(f"originalSource={record.get('originalSource')!r}")
    problems.extend(native_record_resource_problems(record, expected, None))
    return problems


def verify_install_report(report: dict, package: Path, manifest: dict) -> None:
    data = ((report.get("install") or {}).get("body") or {}).get("data") or {}
    if data.get("id") != manifest.get("name", "mindie-agent") or data.get("hasErrors"):
        raise SystemExit(json.dumps(report, indent=2)[:2000])
    expected = expected_native_resources(package, manifest)
    problems = native_record_resource_problems(data, expected, data)
    problems.extend(native_after_inventory_problems(report, package, manifest, expected))
    if problems:
        raise SystemExit(
            "native install did not enable expected MCP/hook/command resources: "
            + ", ".join(problems) + "\n" + json.dumps(report, indent=2)[:1500]
        )


def call(port, token, method, path, data=None, *, timeout: float = 15.0):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if data is None else json.dumps(data).encode(),
        method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=timeout) as res:
            return {"http": res.status, "body": json.load(res)}
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, "body": json.loads(exc.read())}


def wait_ready(port, token_path, process, deadline):
    while time.monotonic() < deadline and process.poll() is None:
        if token_path.exists():
            try:
                token = token_path.read_text(encoding="utf-8").strip()
                remaining = deadline - time.monotonic()
                report = call(
                    port, token, "GET", "/api/v1/plugins",
                    timeout=min(2.0, max(0.2, remaining)),
                )
                return token, report
            except TimeoutError:
                raise
            except (OSError, ValueError):
                pass
        time.sleep(0.4)
    return None, None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kimi-home", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--readback", action="store_true", help="only GET the native plugin registry, no install")
    args = parser.parse_args()
    home = args.kimi_home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    source = str(args.plugin_root.expanduser().resolve())
    if not (home / "config.toml").exists():
        (home / "config.toml").write_text("builtin_product_skills = false\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "KIMI_CODE_HOME": str(home)}
    env.pop("CODEX_THREAD_ID", None)
    started = time.monotonic()
    process = None
    report = {}
    try:
        process = subprocess.Popen(
            [KIMI, "web", "--no-open", "--port", str(port)],
            cwd=str(home),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        token_path = home / "server.token"
        deadline = time.monotonic() + 20
        token, before = wait_ready(port, token_path, process, deadline)
        if token is None:
            from diagnostic_support import failure

            failure(
                "native_install", "native_api_start", "native_unavailable",
                elapsed_ms=(time.monotonic() - started) * 1000,
                exit_code=process.poll(), reportable=False,
            )
            raise SystemExit("native Kimi plugin API did not become ready")
        report["before"] = before
        token = token_path.read_text(encoding="utf-8").strip()
        if not args.readback:
            report["install"] = call(port, token, "POST", "/api/v1/plugins", {"source": source})
        report["after"] = call(port, token, "GET", "/api/v1/plugins")
    except TimeoutError:
        raise
    except Exception as exc:
        from diagnostic_support import failure

        diagnostic = failure(
            "native_install", "native_api", "native_unavailable",
            exception=exc, reportable=False,
            elapsed_ms=(time.monotonic() - started) * 1000,
        )
        incident = diagnostic.get("incident_id")
        raise SystemExit(f"native Kimi plugin API failed ({type(exc).__name__})" + (f"; incident {incident}" if incident else "")) from None
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
                try:
                    process.kill()
                except TimeoutError:
                    raise
                except OSError:
                    pass
                process.wait(timeout=3)
    if args.readback:
        print(json.dumps(report, indent=2))
        return
    manifest = json.loads((args.plugin_root.expanduser().resolve() / "kimi.plugin.json").read_text(encoding="utf-8"))
    try:
        verify_install_report(report, Path(source), manifest)
    except TimeoutError:
        raise
    except Exception as exc:
        from diagnostic_support import failure

        diagnostic = failure(
            "native_install", "readback", "contract_mismatch",
            exception=exc, elapsed_ms=(time.monotonic() - started) * 1000,
        )
        incident = diagnostic.get("incident_id")
        raise SystemExit(f"native Kimi installation readback failed ({type(exc).__name__})" + (f"; incident {incident}" if incident else "")) from None
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
