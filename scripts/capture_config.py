"""[Role] Install-time wiring for deterministic public transcript capture and service launch context.
[Invariants] Verifies the installed transcript redactor is an absolute existing file; declares MINDIE_KIMI_BIN and MINDIE_GIT_BIN in service_launch_context.
[Timeouts & Locks] Subprocess redactor installation bounded at 90s.
[Side-Effect Policy] Installs the verified gitleaks redactor binary via mindie_knowledge.loop.transcript_redaction.
"""

from pathlib import Path
import json
import subprocess


def prepare(python, scripts):
    result = subprocess.run(
        [str(python), "-m", "mindie_knowledge.loop.transcript_redaction"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("could not install the verified transcript redactor")
    path = result.stdout.decode("utf-8").strip()
    if not Path(path).is_absolute() or not Path(path).is_file():
        raise RuntimeError("installed transcript redactor is missing")
    return {
        "capture_mode": "public-transcript",
        "redactor_executable": path,
        "summary_command": [str(python), str(Path(scripts) / "organizer.py")],
        "service_launch_context": {"paths": {}, "environment": ["MINDIE_KIMI_BIN", "MINDIE_GIT_BIN", "MINDIE_DIAGNOSTICS_CONFIG"]},
    }


def prepare_domains(python, adapter, engine, **options):
    """Use the selected core's one catalog and policy-preserving preparation."""
    from bounded import run
    output = run([str(python), '-m', 'mindie_knowledge.loop.domain_catalog'],
                 json.dumps(dict(adapter=adapter, engine=engine, **options)),
                 timeout=90, max_output=65536)
    return json.loads(output)['domain_configs']
