#!/usr/bin/env python3
"""Install this plugin through Kimi's native plugin API in an explicit home.

Never fabricates plugins/installed.json and never deletes a managed root.
Requires --kimi-home so ~/.kimi-code is not the implicit target.
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


def call(port, token, method, path, data=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if data is None else json.dumps(data).encode(),
        method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return {"http": res.status, "body": json.load(res)}
    except urllib.error.HTTPError as exc:
        return {"http": exc.code, "body": json.loads(exc.read())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kimi-home", type=Path, required=True)
    parser.add_argument("--plugin-root", type=Path, default=PLUGIN_ROOT)
    parser.add_argument("--readback", action="store_true",
                        help="only GET the native plugin registry, no install")
    args = parser.parse_args()
    home = args.kimi_home.expanduser().resolve()
    home.mkdir(parents=True, exist_ok=True)
    source = str(args.plugin_root.expanduser().resolve())
    if not (home / "config.toml").exists():
        (home / "config.toml").write_text("builtin_product_skills = false\n")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ)
    env["KIMI_CODE_HOME"] = str(home)
    env.pop("CODEX_THREAD_ID", None)
    log = home / "mindie-native-install.log"
    process = None
    report = {}
    try:
        with log.open("wb") as out:
            # No start_new_session: the owned web server stays in THIS
            # helper's process group, so an outer supervised kill (e.g.
            # bounded.run deadline) terminates helper and server together.
            process = subprocess.Popen(
                [KIMI, "web", "--no-open", "--port", str(port)],
                cwd=str(home),
                env=env,
                stdout=out,
                stderr=out,
            )
            token_path = home / "server.token"
            deadline = time.monotonic() + 20
            ready = False
            while time.monotonic() < deadline and process.poll() is None:
                if token_path.exists():
                    try:
                        token = token_path.read_text().strip()
                        report["before"] = call(port, token, "GET", "/api/v1/plugins")
                        ready = True
                        break
                    except (OSError, ValueError):
                        pass
                time.sleep(0.4)
            if not ready:
                raise SystemExit("native Kimi plugin API did not become ready")
            token = token_path.read_text().strip()
            if not args.readback:
                report["install"] = call(
                    port, token, "POST", "/api/v1/plugins", {"source": source}
                )
            report["after"] = call(port, token, "GET", "/api/v1/plugins")
    finally:
        if process is not None:
            # Clean up ONLY our own direct server child. No killpg: the
            # helper's group may contain unrelated processes, and
            # os.killpg is unavailable on Windows.
            try:
                process.terminate()
            except OSError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait(timeout=3)
    if args.readback:
        print(json.dumps(report, indent=2))
        return
    body = (report.get("install") or {}).get("body") or {}
    data = body.get("data") or {}
    if data.get("hasErrors") or data.get("id") != "mindie-agent":
        raise SystemExit(json.dumps(report, indent=2)[:2000])
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
