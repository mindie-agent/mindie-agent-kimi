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


def _toml_table(path, values):
    out = "\n[" + ".".join(json.dumps(k) for k in path) + "]\n"
    for key, value in values.items():
        if not isinstance(value, dict):
            out += json.dumps(key) + " = " + json.dumps(value) + "\n"
    for key, value in values.items():
        if isinstance(value, dict):
            out += _toml_table(path + [key], value)
    return out


PROVIDER = "managed:kimi-code"


def _table_values(values):
    out = {}
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, dict):
            nested = _table_values(value)
            if nested:
                out[key] = nested
        else:
            out[key] = value
    return out


def prepare_isolated_home(base: Path) -> Path:
    """Private 0600 config with selected provider/model/thinking. No hooks/plugins."""
    import tomllib

    home = base / "kimi-home"
    home.mkdir(mode=0o700, parents=True)
    source = auth_home()
    config_path = source / "config.toml"
    if not config_path.is_file():
        raise RuntimeError("native Kimi config.toml is missing; organizer cannot resolve a model")
    parsed = tomllib.loads(config_path.read_text())
    providers = parsed.get("providers") if isinstance(parsed.get("providers"), dict) else {}
    models = parsed.get("models") if isinstance(parsed.get("models"), dict) else {}
    provider = providers.get(PROVIDER)
    model = models.get(MODEL)
    if not isinstance(provider, dict) or not provider:
        raise RuntimeError("native provider managed:kimi-code is missing; organizer cannot resolve a model")
    if not isinstance(model, dict) or not model:
        raise RuntimeError("native model kimi-code/k3 is missing; organizer cannot resolve a model")
    thinking = parsed.get("thinking")
    if not isinstance(thinking, dict):
        thinking = {"enabled": True, "effort": "max"}
    else:
        thinking = dict(thinking)
        thinking.setdefault("enabled", True)
        thinking.setdefault("effort", "max")
    conf = 'default_model = "kimi-code/k3"\nbuiltin_product_skills = false\n'
    conf += _toml_table(["providers", PROVIDER], _table_values(provider))
    conf += _toml_table(["models", MODEL], _table_values(model))
    conf += _toml_table(["thinking"], _table_values(thinking))
    target = home / "config.toml"
    target.write_text(conf)
    target.chmod(0o600)
    (home / "plugins").mkdir()
    (home / "plugins" / "installed.json").write_text('{"version":1,"plugins":[]}\n')
    (home / "skills").mkdir()
    for name in ("credentials", "oauth"):
        origin = source / name
        dest = home / name
        if origin.exists() and not dest.exists():
            try:
                os.symlink(origin, dest, target_is_directory=origin.is_dir())
            except OSError:
                if origin.is_file():
                    shutil.copy2(origin, dest)
    return home


def doctor_isolated(home: Path) -> str:
    config = Path(home) / "config.toml"
    if not config.is_file():
        raise RuntimeError("isolated organizer config is missing")
    mode = config.stat().st_mode & 0o777
    if mode & 0o077:
        raise RuntimeError("isolated organizer config is not private")
    env = {**os.environ, "KIMI_CODE_HOME": str(home), "KIMI_DISABLE_TELEMETRY": "1"}
    return run(
        [kimi_bin(), "doctor", "config", str(config)],
        "",
        timeout=20,
        env=env,
        cwd=str(home),
    )


def run_native(payload):
    prompt = (
        "Organize this increment. Distinguish initial or failed observations "
        "from verified final settings in every title, summary, and body. "
        "Do not turn case-specific evidence into a universal protocol. "
        "Return only JSON {\"entries\":[...]}.\n\n"
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
