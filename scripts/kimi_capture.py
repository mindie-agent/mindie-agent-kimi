"""[Role] Install-time wiring for deterministic public transcript capture and service launch context.
[Invariants] Verifies the installed transcript redactor is an absolute existing file; declares MINDIE_KIMI_BIN and MINDIE_GIT_BIN in service_launch_context.
[Timeouts & Locks] Subprocess redactor and domain-catalog calls bounded at 90s via `mindie_harness.process` (`bounded.run`).
[Side-Effect Policy] Installs the verified gitleaks redactor binary via `mindie_knowledge.loop.transcript_redaction`.
"""
from __future__ import annotations

import json
from pathlib import Path

PUBLIC_FEEDS = [
    {"repository": "mindie-agent/knowledge-vllm-ascend", "ref": "main",
     "domain": "vllm-ascend", "interval_seconds": 300},
    {"repository": "mindie-agent/knowledge-operators", "ref": "main",
     "domain": "operators", "interval_seconds": 300},
]


def prepare(python, scripts):
    from bounded import run

    try:
        output = run(
            [str(python), "-m", "mindie_knowledge.loop.transcript_redaction"],
            "", timeout=90, max_output=65536,
        )
    except RuntimeError as exc:
        raise RuntimeError("could not install the verified transcript redactor") from exc
    path = output.strip()
    if not Path(path).is_absolute() or not Path(path).is_file():
        raise RuntimeError("installed transcript redactor is missing")
    return {
        "capture_mode": "public-transcript",
        "redactor_executable": path,
        "summary_command": [str(python), str(Path(scripts) / "organizer.py")],
        "service_launch_context": {
            "paths": {},
            "environment": ["MINDIE_KIMI_BIN", "MINDIE_GIT_BIN", "MINDIE_DIAGNOSTICS_CONFIG"],
        },
    }


def prepare_domains(python, adapter, engine, **options):
    """Use the selected core's one catalog and policy-preserving preparation."""
    from bounded import run

    options.setdefault("domains", PUBLIC_FEEDS)
    output = run(
        [str(python), "-m", "mindie_knowledge.loop.domain_catalog"],
        json.dumps(dict(adapter=adapter, engine=engine, **options)),
        timeout=90,
        max_output=65536,
    )
    return json.loads(output)["domain_configs"]
