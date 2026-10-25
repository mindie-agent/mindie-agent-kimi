# mindie-agent-kimi 0.1.4

本版本固定 remote-dev `1fefcc38`；diagnostics 以 `0.5.2` 为 bootstrap floor，实际激活由 component_prepare 选择并保留已安装的 `0.5.3`。大日志命令/TaskOutput 使用核心有界头尾与完整引用；增量页保持游标无损。自动更新包保留 `toolTimeoutMs=660000` 和 `REMOTE_DEV_HARNESS_PROFILE=kimi`，并在 discovery 前绑定 profile。

不可变的 0.1.2 控制器首跳没有 `--overrides`，因此发布包以 0.5.2 为可解析的 bootstrap floor；其真实的 component_prepare 会读取当前运行时 inventory 并选择 0.5.3/knowledge 新版本，最终 prepared generation 不降级。已在私有 fixture 用实际 0.1.2 build_runtime + 实际 component_prepare 验证 0.5.3/1c465a18 保留、knowledge 0.13.4 与 core `1fefcc38`；尚无正式主机激活/loaded-tool 证明。0.1.3 控制器带 overrides。

本地/候选 feed 验证不代表正式 GitHub 自动分发已完成。
