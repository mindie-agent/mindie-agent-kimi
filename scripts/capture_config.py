"""[Role] Install-time wiring for deterministic public transcript capture and service launch context.
[Invariants] Verifies the installed transcript redactor is an absolute existing file; declares MINDIE_KIMI_BIN and MINDIE_GIT_BIN in service_launch_context.
[Timeouts & Locks] Subprocess redactor installation bounded at 90s.
[Side-Effect Policy] Installs the verified gitleaks redactor binary via mindie_knowledge.loop.transcript_redaction.
"""

from pathlib import Path
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
        "service_launch_context": {"paths": {}, "environment": ["MINDIE_KIMI_BIN", "MINDIE_GIT_BIN"]},
    }
