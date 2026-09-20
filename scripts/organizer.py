#!/usr/bin/env python3
"""Native K3 organizer: isolated home, no tools/hooks/plugins.

A missing model or host is a failure, never a successful empty organization.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from bounded import run

MAX_INPUT = 65536
MAX_RESULT = 32768
AGENT_FILE = HERE / "organize-agent.md"
MODEL = "kimi-code/k3"


def convert_conditions(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError("invalid organized entry conditions")
    conditions = {}
    for pair in value:
        if not isinstance(pair, dict) or set(pair) != {"key", "value"}:
            raise ValueError("invalid organized entry conditions")
        key, item = pair["key"], pair["value"]
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise ValueError("invalid organized entry conditions")
        if not isinstance(item, str) or len(item) > 512:
            raise ValueError("invalid organized entry conditions")
        if key in conditions:
            raise ValueError("duplicate condition key")
        conditions[key] = item
    return conditions


def normalize(result):
    if not isinstance(result, dict) or set(result) != {"entries"}:
        raise ValueError("invalid organizer result")
    if not isinstance(result["entries"], list) or len(result["entries"]) > 3:
        raise ValueError("at most three entries per call")
    entries = []
    for entry in result["entries"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid organized entry")
        item = dict(entry)
        if "conditions" in item:
            item["conditions"] = convert_conditions(item["conditions"])
        entries.append(item)
    return dict(entries=entries)


def extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("organizer output is not JSON")
    return json.loads(text[start : end + 1])


def kimi_bin():
    explicit = os.environ.get("MINDIE_KIMI_BIN")
    if explicit:
        return explicit
    found = shutil.which("kimi")
    if found:
        return found
    raise RuntimeError("kimi binary is not on PATH; organizer cannot run")


def auth_home():
    env = os.environ.get("KIMI_CODE_HOME")
    if env:
        return Path(env)
    return Path.home() / ".kimi-code"


def prepare_isolated_home(base: Path) -> Path:
    home = base / "kimi-home"
    home.mkdir(mode=0o700)
    (home / "config.toml").write_text("builtin_product_skills = false\n")
    (home / "plugins").mkdir()
    (home / "plugins" / "installed.json").write_text('{"version":1,"plugins":[]}\n')
    (home / "skills").mkdir()
    source = auth_home()
    for name in ("credentials", "oauth"):
        origin = source / name
        target = home / name
        if origin.exists() and not target.exists():
            try:
                os.symlink(origin, target, target_is_directory=origin.is_dir())
            except OSError:
                if origin.is_file():
                    shutil.copy2(origin, target)
    return home


def run_native(payload):
    prompt = (
        "Organize this increment. Return only JSON {\"entries\":[...]}.\n\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    isolated = Path(tempfile.mkdtemp(prefix="mindie-kimi-organizer-"))
    try:
        home = prepare_isolated_home(isolated)
        empty_skills = home / "skills"
        env = {**os.environ, "KIMI_CODE_HOME": str(home), "KIMI_DISABLE_TELEMETRY": "1"}
        output = run(
            [
                kimi_bin(),
                "-p",
                prompt,
                "--output-format",
                "text",
                "--model",
                MODEL,
                "--agent-file",
                str(AGENT_FILE),
                "--skills-dir",
                str(empty_skills),
            ],
            "",
            timeout=120,
            env=env,
            cwd=str(isolated),
            max_output=MAX_RESULT,
        )
        if not output.strip():
            raise ValueError("organizer produced no output")
        return normalize(extract_json(output))
    finally:
        shutil.rmtree(isolated, ignore_errors=True)


def main():
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise SystemExit("organizer input exceeds limit")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit("organizer payload must be one JSON object")
    print(json.dumps(run_native(payload), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(str(exc)[:400], file=sys.stderr)
        raise SystemExit(2)
