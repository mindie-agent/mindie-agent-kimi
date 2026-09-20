# Acceptance status

Pre-release work, 2026-09-21. The design inherits the [nine MindIE / VAWS principles](https://github.com/mindie-agent/mindie-agent/blob/main/docs/design-principles.md). Root owns design and final acceptance; development checks are diagnostic, not a release verdict.

## Actual native results

The final package was installed through Kimi 2.0.2's native API in an isolated profile. Native inventory confirmed the selected version and source, two MCP servers, two hooks and seven commands. An earlier package appeared enabled but exposed zero MCP servers; that failure led to the relative executable wrapper and resource-level readback now in the installer.

| Case | Actual observation | Scope |
| --- | --- | --- |
| Explicit read-only entry | K3 / max task `session_d625cb9f-cee3-4a0a-baf8-094761efbc42`, candidate `9956b24`: native slash init, persistent read-only choice, query with two hits and 3,394-character full-body reading; 109.03 seconds, two turns | Sharing off; no capture directory or collection turn database; admission enabled, failures zero |
| Live generation switch | Same native task and old MCP processes survived a real candidate switch to `fe2c105`; exact GitHub dependency build and native install/readback; query still returned two hits, the same remote job remained running and was stopped and drained | No reactivation or automatic retry; tests actual staging/switch, not scheduled remote-main resolution |
| Installation independence | K3 / max task `session_f51c74ba-bc06-4ca3-9fe3-40d8931e117f`, implementation `02163b6`: removed inherited `MINDIE_KIMI_CONFIG` and moved the original source checkout after installation; native init/read-only, two-hit query and 2,502-character body read succeeded in 53.2 seconds, two turns | Retained installation-owned code and explicit generated configuration worked; admission enabled, failures zero; no captures or collection turn database; service cleaned up |

The independent task explained how the retrieved experience changes a new NPU correctness check: round the CPU inputs to the tested dtype before computing the FP32 reference, and preserve the distinction between an initial device setting and the verified logical device. It retained the entry's single-device/operator scope. This proves content retrieval and influence on the proposed method; it is not a new NPU execution result.

Earlier Kimi 0.42.0 / K3 max source and fork records verified exclusion of old or inherited material. Final parser/fork behavior still needs proportionate confirmation against the current native host.

## Remaining acceptance

The organizer previously produced useful content, but its summary confused an initial device setting with the verified final setting. The revised prompt separates initial, failed and verified observations. Its later native Kimi 2.0.2 / K3 max run reached the 120-second deadline without output and stopped without retry. Revised content quality remains unverified.

The final Stop → organizer → exact GitHub receipt → confirmed-body cleanup → Bot → independent consumer path remains pending. Optional feedback and withdrawal are not established by read-only checks. A changed candidate does not inherit all older acceptance evidence automatically.

Local checks at `02163b6` cover 91 cases, including bounded hooks, actual inventory validation, immutable initial code, configuration binding and unknown-write handling. Linux/macOS CI passed for this candidate. Windows code and task registration remain unverified on a real Windows desktop, which the user will supply later. Current updates track `main`; release tracking and old business Skills, including profiling, remain deferred.
