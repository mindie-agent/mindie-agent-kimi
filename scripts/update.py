#!/usr/bin/env python3
"""Update using Kimi's native plugin API plus core knowledge sync.

Kimi 0.42.0 does not auto-update local-path plugins; official plugins only
prompt on next use. This reuses POST /api/v1/plugins (same native install
path) when a Kimi server token exists, then runs mindie_knowledge sync.
No second updater platform.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run
from paths import PLUGIN_ROOT, configured_python, engine_config_path, kimi_home_from_env


def native_plugin_reinstall(home: Path):
    token_path = home / "server.token"
    if not token_path.is_file():
        return {
            "status": "host-notify-only",
            "detail": (
                "Kimi 0.42.0 has no automatic local-path plugin updater. "
                "When kimi web is running, POST /api/v1/plugins with source="
                f"{PLUGIN_ROOT} (scripts/install_kimi_plugin.py). "
                "CLI [upgrade] auto_install in tui.toml updates the kimi binary only."
            ),
        }
    import urllib.request

    token = token_path.read_text().strip()
    port = os.environ.get("KIMI_WEB_PORT")
    if not port:
        return {
            "status": "host-notify-only",
            "detail": "server.token present but KIMI_WEB_PORT unset; use native install helper",
        }
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/v1/plugins",
        data=json.dumps({"source": str(PLUGIN_ROOT)}).encode(),
        method="POST",
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as res:
        return {"status": "native-reinstall", "http": res.status, "body": json.load(res)}


def main():
    report = {}
    home = kimi_home_from_env()
    if home is not None:
        try:
            report["plugin"] = native_plugin_reinstall(home)
        except Exception as exc:
            report["plugin"] = {"status": "failed", "detail": str(exc)[:300]}
    else:
        report["plugin"] = {
            "status": "host-notify-only",
            "detail": "KIMI_CODE_HOME unset; native plugin reinstall not attempted",
        }
    try:
        output = run(
            [
                configured_python(),
                "-m",
                "mindie_knowledge.loop.cli",
                "sync",
                "--config",
                str(engine_config_path()),
            ],
            "",
            timeout=40,
        )
        report["knowledge_sync"] = json.loads(output) if output.strip() else output
    except Exception as exc:
        report["knowledge_sync"] = {"status": "failed", "detail": str(exc)[:300]}
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc)[:400], file=sys.stderr)
        raise SystemExit(2)
