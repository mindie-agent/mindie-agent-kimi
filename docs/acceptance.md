# Acceptance status

Pre-release work, 2026-09-21. The design inherits the [nine MindIE / VAWS principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). Root owns design and final acceptance; development checks are diagnostic, not a release verdict.

## Actual native results

The final package was installed through Kimi 2.0.2's native API in an isolated profile. Native inventory confirmed the selected version and source, two MCP servers, two hooks and seven commands. An earlier package appeared enabled but exposed zero MCP servers; that failure led to the relative executable wrapper and resource-level readback now in the installer.

| Case | Actual observation | Scope |
| --- | --- | --- |
| Explicit read-only entry | K3 / max task `session_d625cb9f-cee3-4a0a-baf8-094761efbc42`, candidate `9956b24`: native slash init, persistent read-only choice, query with two hits and 3,394-character full-body reading; 109.03 seconds, two turns | Sharing off; no capture directory or collection turn database; admission enabled, failures zero |
| Live generation switch | Same native task and old MCP processes survived a real candidate switch to `fe2c105`; exact GitHub dependency build and native install/readback; query still returned two hits, the same remote job remained running and was stopped and drained | No reactivation or automatic retry; tests actual staging/switch, not scheduled remote-main resolution |
| Installation independence | K3 / max task `session_f51c74ba-bc06-4ca3-9fe3-40d8931e117f`, implementation `02163b6`: removed inherited `MINDIE_KIMI_CONFIG` and moved the original source checkout after installation; native init/read-only, two-hit query and 2,502-character body read succeeded in 53.2 seconds, two turns | Retained installation-owned code and explicit generated configuration worked; admission enabled, failures zero; no captures or collection turn database; service cleaned up |
| Native install-result-loss rollback | Candidate `ceae00c` installed through the real native API and exact resource readback; injected result loss then restored the retained `fe2c105` package and complete current runtime tuple with native readback in 6.92 seconds | Two native installs, zero model calls, automatic retries or global changes |

The independent task explained how the retrieved experience changes a new NPU correctness check: round the CPU inputs to the tested dtype before computing the FP32 reference, and preserve the distinction between an initial device setting and the verified logical device. It retained the entry's single-device/operator scope. This proves content retrieval and influence on the proposed method; it is not a new NPU execution result.

Earlier Kimi 0.42.0 / K3 max source and fork records verified exclusion of old or inherited material. Final parser/fork behavior still needs proportionate confirmation against the current native host.

## Remaining acceptance

Organizer evidence is mixed. An earlier Kimi 2.0.2 / K3 max request reached the 120-second deadline without output and stopped without retry. A later request using the public answer from real NPU task `01a0be98-8596-7d41-8e9a-e6b97112453a` returned in 50.32 seconds, preserving numeric results but inventing a failed run at an initial device value. The reviewed prompt now distinguishes changed, untested, failed and verified states and requires evidence before calling a setting failed.

One native K3 / max invocation of that revised prompt returned in 54.1 seconds, with zero retries. It preserved the FP16/BF16 numbers, dtype-rounded reference method, single-device scope and unknown physical mapping, and no longer invented a failed run. It nevertheless asserted that the initial value had never been executed, which the supplied public material did not establish; it also split the closely related case into two verbose entries. Thus local invocation and much of the evidence were preserved, but generation quality is not accepted as complete. No entry from these checks was published, and they do not constitute a new NPU run or the Stop/publication loop.

The final Stop → organizer → exact GitHub receipt → confirmed-body cleanup → Bot → independent consumer path remains pending. Optional feedback and withdrawal are not established by read-only checks. A changed candidate does not inherit all older acceptance evidence automatically.

Local checks at `02163b6` cover 91 cases, including bounded hooks, actual inventory validation, immutable initial code, configuration binding and unknown-write handling. Linux/macOS CI passed for this candidate. Eight focused organizer/admission checks passed after the narrow prompt edit; these checks do not establish content quality. The reviewed adapter can merge for integrated work on main. Windows code and task registration remain unverified on a real Windows desktop, which the user will supply after merge; Windows is not a gate for this merge. Current updates track `main`; release tracking and old business Skills, including profiling, remain deferred.
