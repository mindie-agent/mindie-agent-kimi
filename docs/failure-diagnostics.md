# Failure diagnosis and local cancellation

Native status reports existing knowledge configuration, service/storage health,
this task's admission, a shared maintenance pause, and the latest five captures
and contribution batches associated with the bound task. An enabled or paused
task can call status after a later failure without another user slash command;
unadmitted tasks still require the explicit native status command. An absent identity
returns no task records. Failed batch IDs let recover inspect the existing write;
unknown publication results must be reconciled before any explicit retry.

Status does not initialize databases, start models/services, activate a task,
reset a pause, or replay consumed input. SQLite read-only access may create normal
WAL reader sidecars; it does not change business records. Failure projections omit
raw provider stderr, transcript text, tokens and other task identities. Existing
malformed adapter settings produce a diagnostic instead of first-use choices.
Native shell/SSH and independent remote-dev remain available when knowledge is down.

The stdio front keeps input responsive while bounded tool requests run. Cancel and
EOF clean up only owned local processes; a cancelled observation does not establish
remote job termination. Responses preserve the original job reference and delivery
certainty. Explicitly inspect or stop that job before a new submission. Remote poll
waiting is capped at 30 seconds without changing the remote workload timeout.

Actual macOS evidence includes task-isolated SQLite diagnostics, paused state with
unchanged counters, corrupt config, child/grandchild cancellation, ping while busy,
EOF, output backpressure, update-lock cancellation and safe remote exception
classification. Development checks protect those contracts. Controlled native
identity/helper seams are not evidence that the host UI emitted a cancel event,
that a remote job stopped, or that a full contribution loop completed.

Windows remains unverified on hardware. Its blocking pipe-write and process-tree
cleanup behavior requires separate acceptance. No new retry or background service
is introduced by these changes.
