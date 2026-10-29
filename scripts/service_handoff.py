"""[Role] Updater-only bounded knowledge service stop/restore handoff helper.
[Invariants] Verifies loopback endpoint and waits for OS consumer.lock release before confirming stop; never leaks connection tokens in stdout.
[Timeouts & Locks] stop_if_idle RPC timeout 1.0s + 3.0s consumer.lock release poll; restore ensure_service 5.0s + 0.5s status probe.
[Side-Effect Policy] Stops idle knowledge service or restarts service when active admission leases exist.
"""

import json
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse

from mindie_knowledge.loop.activation import Admission
from mindie_knowledge.loop.cli import config_at, connect, ensure_service, rpc
from mindie_knowledge.loop.locks import lock_held


def refused(exc):
    return isinstance(exc, ConnectionRefusedError) or (
        isinstance(exc, URLError) and isinstance(exc.reason, ConnectionRefusedError)
    )


def stop(engine):
    config = config_at(engine)
    consumer = Path(config["root"]) / config["domain"] / "consumer.lock"
    if lock_held(consumer) is False:
        return {"idle": True, "service": "absent"}
    try:
        connection = connect(config)
    except FileNotFoundError:
        return {"idle": True, "service": "absent"}
    if urlparse(connection["url"]).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("service endpoint is not loopback")
    try:
        result = rpc(connection, "stop_if_idle", timeout=1)
    except TimeoutError:
        raise
    except OSError as exc:
        if refused(exc) and lock_held(consumer) is False:
            return {"idle": True, "service": "absent"}
        raise RuntimeError("stop acknowledgement unavailable; no restart") from None
    if not isinstance(result, dict) or type(result.get("idle")) is not bool:
        raise RuntimeError("invalid stop acknowledgement; no restart")
    if not result["idle"]:
        return {"idle": False, "service": "busy"}
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if lock_held(consumer) is False:
            return {"idle": True, "service": "stopped"}
        time.sleep(min(0.1, max(0, deadline - time.monotonic())))
    raise RuntimeError("old service exit unconfirmed; no restart")


def restore(engine):
    config = config_at(engine)
    path = config.get("admission_path")
    if not path or not Admission(path).leases():
        return {"status": "not-needed", "reason": "no-valid-lease"}
    connection = ensure_service(engine)
    if rpc(connection, "status", timeout=0.5).get("admission_frozen") is not False:
        raise RuntimeError("selected service remains frozen; no restart")
    return {"status": "restored"}


if __name__ == "__main__":
    try:
        action, engine = sys.argv[1:]
        print(json.dumps({"stop": stop, "restore": restore}[action](engine)))
    except TimeoutError:
        raise
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": type(exc).__name__}))
        raise SystemExit(1)
