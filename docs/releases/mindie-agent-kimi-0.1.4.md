# mindie-agent-kimi 0.1.4

本版本固定 remote-dev `2bd3a9e`；运行时代码验收实际测于 core `1fefcc38`，`2bd3a9e` 仅新增发布说明与路径、无运行时差异。diagnostics 以 `0.5.2` 为 bootstrap floor；实际激活由 component_prepare 选择并保留已安装的 `0.5.3`。大日志命令/TaskOutput 使用核心有界头尾与完整原始远端引用；增量页保持游标无损。自动更新包保留 `toolTimeoutMs=660000` 和 `REMOTE_DEV_HARNESS_PROFILE=kimi`，并在 discovery 前绑定 profile。

不可变的 0.1.2 控制器首跳没有 `--overrides`，因此发布包以 0.5.2 为可解析的 bootstrap floor；其真实的 component_prepare 会读取当前运行时 inventory 并选择 0.5.3/knowledge 新版本，最终 prepared generation 不降级。父级实际旧控制器证据（`parent-kimi-upgrade-0.1.2-6a14d4faa2`、`parent-kimi-upgrade-0.1.3-c45df72748`）在隔离 Kimi home 中完成旧 0.1.2/0.1.3 build_runtime -> component_prepare -> build_host_package -> 实际 switch/native_install；随后候选正常 updater 修复旧包，已注册的 `mindie_launch.py` 前端返回 29 个工具，timeout `660000`、profile `kimi`、diagnostics `0.5.3` 均正确。实际源码验收为 Kimi `b8dbc14`/core `1fefcc3`；之后的升级模块未改，core `2bd3a9e` 为文档-only。fresh setup 的 `build_bootstrap_runtime` 也经 `parent-kimi-fresh-runtime-077fc3046a` 实测得到 0.5.3/1c465a18、knowledge 0.13.3/38a、core 1fef。

但是两次正常 updater check 均以 exit 1 结束，原因是正式 feed 返回 `no_compatible_release`/`skipped_undeclared_releases7`，同时 `host_package_repair` 已成功安装；未重放不确定写入。该证据证明候选迁移/修复/已加载前端，不等同于未发布 release 的正式发现在线或已分发。0.1.3 控制器带 overrides。

本地/候选 feed 验证不代表正式 GitHub 自动分发已完成。
