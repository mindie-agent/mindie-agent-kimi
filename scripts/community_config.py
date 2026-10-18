"""[Role] Subprocess bridge writing community sharing configuration via the pinned knowledge runtime.
[Invariants] Executes CommunityWriteContext.configure inside the target runtime interpreter so schema validation and locking stay canonical.
[Timeouts & Locks] Subprocess execution bounded at 30s; acquires CommunityWriteContext file lock inside the child.
[Side-Effect Policy] Atomically writes the community settings JSON file or preserves existing bytes on failure.
"""

import json
import subprocess
import sys

SCRIPT = """
import json, sys
from mindie_knowledge.loop.settings import CommunityWriteContext
request = json.load(sys.stdin)
with CommunityWriteContext(request['path']) as context:
    result = context.configure(request['path'], request['settings'])
print(json.dumps(result.raw))
"""


def configure(path, settings, python=None):
    completed = subprocess.run(
        [str(python or sys.executable), "-c", SCRIPT],
        input=json.dumps({"path": str(path), "settings": settings}).encode("utf-8"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode:
        raise SystemExit("shared community configuration failed; nothing was written; existing bytes preserved")
    return json.loads(completed.stdout)
