# Domain tooling

Remote-dev is a general tool. Serving or calling it never activates MindIE,
touches a knowledge lease, or creates capture state.

Jobs and artifacts are owned by the native Kimi session that bound the
`request_nonce`. Do not operate on another task's `job_id`. After a plugin
update, poll or stop an existing job with the same id; do not mix an old
script with a new interpreter on one call.

If a remote mutation's result is uncertain, inspect its existing receipt or
job before deciding what to do. Explicit recovery of a paused remote failure
circuit is `/mindie-agent:recover` in this native task — still without a
model-selected session id.
