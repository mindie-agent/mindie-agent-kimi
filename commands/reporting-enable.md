---
description: reporting-enable for independent product failure reporting
---

Call `mindie_entry` once with `op=reporting-enable` and a fresh `request_nonce`. The current native command authorizes this operation; never pass a session id or infer authorization from model arguments. If the native result returns run_outside_hook=true, execute the returned `command_line` once outside the Hook (`command` is the same argument vector). Report readiness from the result; on failure report degraded and do not retry. Show the returned shared reporting state.
$ARGUMENTS
