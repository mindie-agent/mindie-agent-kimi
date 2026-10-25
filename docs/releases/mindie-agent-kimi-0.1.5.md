# mindie-agent-kimi 0.1.5

本版本不改变插件运行时行为。发布工作流在测试前从同一仓库显式取入两个固定历史提交：0.1.2 的 `7aecfd22857c313b52d6fe5923292f297884fec0` 与 0.1.3 的 `faf199fb7c76a63e9ff5f94cb1362ebdf203f5a3`。因此 depth=1 的 Release 检出也能使用真实旧更新器源码执行 `OldControllerSourceTests`，不再依赖完整历史。测试逻辑、断言与跳过条件均未修改。

运行时保持 remote-dev 0.10.7（`4c40cb5`）与 diagnostics 0.5.4（`5ae643e`），bootstrap 与活跃组件 pins 不变。隔离 depth=1 复现中，两个旧提交在 fetch 前均不可 `git archive`，执行同一 fetch 后 `OldControllerSourceTests` 全部通过。

该修订只补齐发布测试前置，不改变 0.1.2/0.1.3 旧更新器兼容性、安装绑定或已有会话授权。
