# mindie-agent-kimi 0.1.5

This release does not change plugin runtime behavior. Before tests, the release workflow now explicitly fetches two fixed historical commits from the same repository: `7aecfd22857c313b52d6fe5923292f297884fec0` for 0.1.2 and `faf199fb7c76a63e9ff5f94cb1362ebdf203f5a3` for 0.1.3. A depth=1 release checkout can therefore run `OldControllerSourceTests` against the real legacy updater sources instead of relying on full history. Test logic, assertions, and skip conditions are unchanged.

The runtime remains remote-dev 0.10.7 (`4c40cb5`) and diagnostics 0.5.4 (`5ae643e`); bootstrap and active component pins are unchanged. In an isolated depth=1 reproduction, neither legacy commit could be archived before the fetch, and `OldControllerSourceTests` passed after the same fetch.

This revision only fills the release-test prerequisite; it does not change 0.1.2/0.1.3 legacy updater compatibility, installation bindings, or existing session consent.
