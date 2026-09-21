# Organizer failure and cancellation evidence

The adapter pins knowledge core `59b6ec4fd2ddf1ec1c95e5f635fb6b9076737a69`.
Failures report a static category through the shared exit-code contract;
the core retains the category, exit status and elapsed seconds. Provider stderr
is never copied into the persisted diagnostic. An older exit 2 stays unknown.
The existing 120-second native / 125-second outer deadlines, output bounds,
single attempt and consumed-input policy are unchanged.

The previous POSIX runner always created a separate native process group.
An actual macOS process reproduction showed the organizer exiting on core
cancellation while its native child survived. A maintenance runner now keeps
that child in the core-owned group. Local adapter cleanup kills only the direct
child; the core closes the entire group on every terminal path. Standalone
commands still own an independent group.

Validation on 2026-09-21:

- Seven actual local process cases verified configuration, input, native,
  invalid-result, output-limit, deadline and success behavior. The deadline
  case ran for 120.073 seconds. All diagnostics were static and no private
  sentinel escaped. These cases used a controlled executable in the native
  boundary, not a model.
- With the official core installed and no source-path override, actual
  organizer/native/grandchild processes shared one owned group. Cancellation
  completed in 0.948 seconds; a normal successful result completed in 0.598
  seconds. Every owned process was gone in both cases.
- Independent core checks covered unknown legacy exit 2, raw stderr discard,
  output limits and the closed-pipes deadline path.
- A single real Kimi K3/max organizer invocation then used new material from
  these actual local checks. It returned one faithful record in 34.371 seconds,
  with no retry or publication. Process sampling confirmed the organizer and
  native Kimi shared the core-owned group and left no owned process behind.
  The isolated profile selected K3/max, had no plugins, skills or MCP, and was
  removed afterward; the original profile was unchanged. This was an
  independent organizer call, not a new Stop-to-publication test.

No failed capture was replayed, no retry budget was reset, and no knowledge PR
was created by these diagnostics. The earlier retained-task organizer failure
cannot be retrospectively classified from its elapsed time. These process
checks do not prove native Stop-to-publication delivery or Windows behavior.
