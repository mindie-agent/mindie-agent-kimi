# Fault diagnostics — 2026-09-22

Own-tool failures now preserve the original result and attach a bounded local
incident. Trusted inner references pass through instead of creating duplicate
reports. Reporting is independently opt-in; status is read-only and install
leaves uploads off. The startup fallback is copied verbatim from the shared
package and never installs dependencies or uploads. Existing updater checks run
offline maintenance, preserve degraded results and do not replay business work.

The candidate fixture was installed through the real native Kimi plugin API.
An initial correctly configured task stopped before tool execution with
`OAuthUnauthorizedError` / `The provided authorization grant is invalid`;
that failed result is retained. After the user explicitly logged in again,
a fresh Kimi Code 2.0.2 task completed in 29.299 seconds with one native turn
and exactly one `remote_bash` call. Both native request steps recorded model
`k3`, alias `kimi-code/k3` and `thinkingEffort=max`; these fields show the
requested effort, not the provider's internal reasoning implementation.

The real copied candidate front received malformed JSON from a test-only
one-shot helper and returned `helper_protocol`, an incident reference and the
read-only `/mindie-agent:reporting-status` recovery hint. The single local
`protocol_mismatch` diagnostic matched the incident repeated in the model's
answer. Reporting remained unauthorized; there was no Issue upload, second
tool call, retry, SSH, knowledge activation or capture. The helper intentionally
stops before business execution. Temporary credential links/provider settings
and owned processes were cleaned up; the global configuration was unchanged.

This proves native failure delivery and consumption of the incident/recovery
hint. It does not prove complete answer fidelity: after correctly describing
the remote outcome as unconfirmed, the model also said the business state was
unaffected, which exceeded its tool evidence. That original answer is retained
and was not repaired by another model call. The fixture used a copied candidate
and editable development dependencies, so this is not final-package/exact-pin
installation, native SSH execution or the Stop-to-publication loop.

An earlier harness combined incompatible prompt/auto flags and was rejected
before the model. It is retained as a failed setup attempt, not a model retry.
No production authentication or upload setting was changed.

A separate model-free installation then exercised the unmodified candidate's
normal `setup.py` bootstrap in an empty isolated Kimi home. It created its own
virtual environment from the final official requirements, without a runtime
override or editable packages. Installed VCS metadata and module paths verified:

- `mindie-knowledge` 0.8.1 at `87deb0717e9b03603b5ce46b490da14f3119c37d`.
- `remote-dev` 0.9.4 at `fb441aa18f0dcd5aff9b4a2f94a39dc4070c85e3`.
- `mindie-diagnostics` 0.3.0 at `4a7e50622492c92089c2318d80dbd2a24d41f145`.

`pip check` and native Kimi Code 2.0.2 `doctor config` passed. The real native
plugin API installed and read back `0.1.0+mindie.bootstrap` as enabled, `ok`,
without errors, with two enabled MCP servers, two hooks and ten commands. Both
installed manifest commands completed actual `initialize` and `tools/list`
exchanges and exited normally: four knowledge tools and eighteen remote tools.
No session identity or invocation nonce was fabricated; no tool was executed.

The installed diagnostics CLI reported `not_configured` for the empty local
profile and, when pointed read-only at the earlier owned native case's logs,
returned its existing incident and record reference. The earlier log hashes
were unchanged. Community sharing remained off, no reporting policy, reporter
queue, admission database or model history was created, and no schedule was
registered. No credentials were copied or linked. Global Kimi configuration
was unchanged, the temporary native server token was removed, and no owned
process remained.

This separate case proves official dependency packaging and native candidate
installation on macOS. The adapter was still a working-tree candidate whose
source hashes stayed unchanged during this check; it does not establish that
the adapter itself was already released. It does not upgrade the earlier
model-answer fidelity result or prove native SSH, Stop-to-publication, or
Windows acceptance.

Shared actual log, transport, GitHub and macOS service evidence: [diagnostics acceptance](https://github.com/mindie-agent/diagnostics/blob/main/docs/dfx-acceptance-2026-09-22.md). Windows hardware remains unverified.
