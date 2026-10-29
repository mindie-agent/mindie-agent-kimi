"""[Role] Pure Kimi remote subprocess deadline arithmetic.

[Invariants]
- The outer deadline includes native waiting, transport and cleanup budgets.
- Background launch returns promptly and retains the minimum transport budget.

[Timeouts & Locks]
- No waits or locks; callers own execution and apply these derived limits.

[Side-Effect Policy]
- No I/O, process creation or runtime imports.
"""

import math

# Remote child budget: native Kimi Bash foreground waits up to 300s, Read/
# Glob/Grep up to 300s; SSH setup, framing, pipe drain and cleanup happen
# outside that wait. A fixed 60s outer deadline would cut off a legal call.
REMOTE_GRACE_SECONDS = 60.0
REMOTE_MIN_BUDGET_SECONDS = 180.0
KIMI_REMOTE_INNER_SECONDS = {
    "remote_bash": 300.0,
    "remote_read": 300.0,
    "remote_glob": 300.0,
    "remote_grep": 300.0,
    "remote_readmediafile": 300.0,
}

def kimi_remote_inner_seconds(name, arguments):
    """Largest legal native inner wait for this Kimi remote tool, in seconds.

    Kimi Bash takes ``timeout`` in seconds; canonical aliases take
    ``yield_time_ms``/``timeout_ms`` in milliseconds. A background launch
    returns immediately, so only the minimum remote budget is needed.
    """
    key = name.rsplit("__", 1)[-1].lower() if isinstance(name, str) else ""
    if isinstance(arguments, dict) and arguments.get("run_in_background") is True:
        return 0.0
    native = KIMI_REMOTE_INNER_SECONDS.get(key, 0.0)
    explicit = 0.0
    if isinstance(arguments, dict):
        for field in ("timeout", "yield_time_ms", "timeout_ms"):
            value = arguments.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            # Kimi Bash's native timeout is seconds; transport waits are ms.
            seconds = float(value) if field == "timeout" and key in {
                "remote_bash", "bash"} else float(value) / 1000.0
            explicit = max(explicit, seconds)
    return max(native, explicit)


def remote_budget(name, arguments=None, *, native_wait_ms=None,
                  grace=REMOTE_GRACE_SECONDS, minimum=REMOTE_MIN_BUDGET_SECONDS):
    """Outer child budget: native wait + SSH/framing/drain/cleanup grace.

    Pure and injectable so the arithmetic is proven without a 300s sleep.
    """
    if isinstance(grace, bool) or not isinstance(grace, (int, float)) \
            or not math.isfinite(grace) or grace <= 0:
        raise ValueError("remote grace must be a positive finite number")
    if isinstance(minimum, bool) or not isinstance(minimum, (int, float)) \
            or not math.isfinite(minimum) or minimum <= 0:
        raise ValueError("remote minimum budget must be a positive finite number")
    if native_wait_ms is None:
        wait = kimi_remote_inner_seconds(name, arguments)
    else:
        if isinstance(native_wait_ms, bool) or not isinstance(native_wait_ms, (int, float)) \
                or not math.isfinite(native_wait_ms) or native_wait_ms < 0:
            raise ValueError("native wait must be a nonnegative finite number of milliseconds")
        wait = float(native_wait_ms) / 1000.0
    return max(float(minimum), wait + float(grace))
