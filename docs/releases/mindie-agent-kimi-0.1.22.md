# mindie-agent-kimi 0.1.22

[English](mindie-agent-kimi-0.1.22.en.md)

- **Diagnostics 2.0 崩溃收据鉴权与 Triage 归因升级（`diagnostic_support.py`、`component_tools.py`）**：接入 `MINDIE_DIAGNOSTICS_INVOCATION_TOKEN` 子进程崩溃收据校验、40 位源码 SHA 归因与结构化 `reason_code`/`substage` 透传，在 `runtime/active-overrides.txt` 升级 `diagnostics v0.5.11`、`debugger v0.1.2`、`knowledge v0.13.17`、`remote-dev v0.11.10` 与 `coordinator v0.7.9`。
