# Service continuity during plugin updates

An updater used to stop an idle knowledge service and install the next plugin
without restoring the service. A later query hid the defect by starting it;
a task that only used remote development could then deliver a Stop event to
no service and lose that increment.

The update transaction now confirms that the exact old loopback endpoint has
exited, switches the native package and committed runtime tuple, and makes
one bounded restoration attempt while retaining the exclusive operation lock.
Restoration uses the selected interpreter and engine configuration directly,
never a shared-lock launcher. Core startup remains one spawn, five seconds,
and three readiness probes; the returned service must be unfrozen.

Only a service stopped by this updater invocation is eligible. An absent
service stays absent; revoked or circuit-paused task leases do not start a
service. Stop hooks still only notify an existing service. No task is activated,
no transcript is replayed, and no model call belongs to the updater.

Rollback restores a service only after both the retained native package and
the exact old pointer are proven restored. Failed or interrupted handoff is
kept in the existing updater status as service_handoff. Same-revision checks
and successful Feed sync cannot hide it. A later scheduler invocation does
not interpret that marker as permission to spawn or retry restoration.

The selected native profile is retained for the replacement service. Claude
Code imports only supported provider/model/effort values from that profile's
user settings into its isolated organizer; it does not import hooks, tools,
MCP configuration or project settings. Secret values never enter updater status.

## Validation boundary

Real local-daemon acceptance covers live, absent, revoked and rollback paths
for Kimi and Claude Code. Those cases use a controlled native-install boundary;
they establish actual process/endpoint/admission behavior, not native package
installation or model authentication. Native acceptance is recorded separately.
Windows real-machine acceptance remains with the maintainer after merge.

Real Kimi Code 2.0.2 acceptance installed candidate `cd7728e0` through the native
API, read back loaded resources and preserved an existing task lease. An operator
first established the old live service as an explicit test precondition. The
actual switch replaced its PID/endpoint with the selected unfrozen service; no
post-update ensure or knowledge query occurred. The retained task then completed
one K3/max continuation in 27.3 s with zero tools and one native model request.
Its genuine Stop produced a new capture before turn completion.

The background organizer attempted that new capture once and exited 2. No entry
or PR was generated, and the failed input was not replayed. The first terminal
observation was about 128 s after admission; a 120 s organizer deadline is a
possible explanation, but the core discards child stderr, so the precise cause
is not established. Model-free native config validation passed with the selected
K3/max profile. This validates update-to-Stop delivery, not successful Kimi
business-experience organization/publication. That remaining gap is explicit.
The owned daemon was stopped and its endpoint/process absence verified.

The harness used production stage/switch functions directly, then explicitly
applied their normal final status aggregation; it was not a scheduled full check.
A preflight harness argument error occurred before any installation/model action
and remains separately recorded. Component checks: 105 passed. Real-daemon
rollback/no-service/no-lease checks and interrupted-parent pending-state evidence
are recorded separately from the native package run.
