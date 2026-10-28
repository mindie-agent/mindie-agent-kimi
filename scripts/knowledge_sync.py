"""[Role] Run explicit feed synchronization through the shared knowledge CLI.

[Invariants]
- The MCP adapter must verify the current session's target-domain lease first.
- Sync is a standalone CLI operation, not a knowledge-service RPC.
- Feed failures and deferred rows remain visible even when the CLI exits zero.

[Timeouts & Locks]
- One bounded 120s child, plus process cleanup; the launcher reserves 150s
  including native nonce claim/finish and target-domain admission reads.
- The shared core owns feed locking and publication validation.

[Side-Effect Policy]
- Updates only the configured knowledge feeds and local derived indexes.
- Never starts a knowledge service or retries a failed synchronization.
"""
import os

from bounded import run
from paths import load_adapter_config
from updater_check import fold_feed_results

TIMEOUT = 120.0
TOOL = dict(
    name="knowledge_sync",
    description=(
        "Synchronize the configured knowledge feeds after explicit /mindie-agent "
        "activation in this session and target domain. Keeps each feed receipt, "
        "including deferred or unavailable results. Does not start a knowledge service."
    ),
    inputSchema=dict(
        type="object", additionalProperties=False, properties={
            "domain": {"type": "string", "description": "Installed target domain; omit for the default."},
        },
    ),
)


def execute(engine_path):
    adapter = load_adapter_config()
    environment = dict(os.environ)
    if adapter.get("git_bin"):
        environment["MINDIE_GIT_BIN"] = adapter["git_bin"]
    process = run(
        [adapter["python"], "-m", "mindie_knowledge.loop.cli", "sync", "--config", str(engine_path)],
        "", timeout=TIMEOUT, max_output=131072, env=environment,
        check=False, capture_result=True,
    )
    result = dict(status="sync_failed", exit_code=process.returncode,
                  stdout=process.stdout, stderr=process.stderr)
    if process.returncode == 0:
        status, rows, detail = fold_feed_results(process.stdout)
        result.update(status=status, feeds=rows)
        if detail:
            result["detail"] = detail
    if getattr(process, "cleanup_error", None):
        result.update(cleanup_status="failed", cleanup_error=process.cleanup_error)
    return result
