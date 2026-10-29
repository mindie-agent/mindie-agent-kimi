# mindie-agent-kimi 0.1.9

This release makes knowledge retrieval depend on missing task information and
updates standard component command bindings with the managed runtime.

## Task routing

When a stack trace or local code already identifies the file, function and
invariant, the entry Skill permits direct inspection, a focused change and
verification without a knowledge call. Missing environment facts are probed
first. An exact knowledge ref can be read directly; retrieval stops when the
available information supports the next action and can reopen when new evidence
reveals a gap.

Batch `refs` with `observed_conditions` remain the preferred explain interface.
Single-ref compatibility requests share the same complete dependency-closure
and final-response budget: a body is present only when loaded, with unresolved
or already-known references reported explicitly. No classifier, mandatory
retrieval count or skip-feedback call is introduced. Native Skill activation,
nonce binding, verification and contribution consent retain their contracts.

## Component updates

Standard Python module commands for optional components contribute their
installed versions as update floors. Official component sources are selected
by the shared runtime; the existing idle generation transaction rebinds those
commands and updates Kimi's stable profile. Command edits during preparation or
handoff defer the switch, and rollback preserves unrelated profile edits.
Custom wrappers remain owned by the user.

Candidate runtime installation now consumes `runtime-active-overrides.txt`.
The immutable bootstrap dependencies used by the old 0.1.2 controller remain
unchanged. The selected active runtime and the bootstrap runtime are separate
facts; installation alone does not prove that the native host has loaded a new
generation.

## Runtime and verification

The active runtime selects knowledge 0.13.6
(`9af5a82ad557e2120277693854a0571613c8a115`), remote-dev 0.11.1 and diagnostics
0.5.4. Exact source revisions are recorded in `runtime-active-overrides.txt`.
The optional test component pins remain profiler 0.5.1, sim 0.7.1 and
coordinator 0.7.0.

The release retains the declared Kimi Code support for 2.1.1, 2.2.0 and 2.3.1.
CI covers installed component CLI execution, stable-profile authority,
concurrent command edits, rollback, isolated startup and native tool contracts
on Linux, macOS and Windows. The tag workflow verifies formal dependency
releases and publishes the exact-source archive, compatibility metadata,
checksums and public-transcript microbenchmark reports.

These contract and CPU microbenchmark results do not establish an end-to-end
task speedup. Earlier task measurements include environment noise and a complex
task gate that did not pass. Native installed version, active runtime version
and post-release automatic upgrade acceptance are recorded separately.
