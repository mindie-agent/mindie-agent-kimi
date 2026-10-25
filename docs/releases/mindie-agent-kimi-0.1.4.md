# mindie-agent-kimi 0.1.4

本版本 active 运行时固定 remote-dev `4c40cb5` 与 mindie-diagnostics 0.5.4（`5ae643e`），由 `runtime-active-overrides.txt` 选择。无 overrides 的不可变 0.1.2 首跳仍使用一致的 bootstrap 三元组：diagnostics 0.5.2 `801a01b`、remote-dev 0.10.6 `0463a13`、knowledge 0.13.3 `38a42f6`。core 相对实测 `1fefcc3` 的代码差异仅为发布说明/路径与依赖 pin；diagnostics 0.5.4 在 reader-registry/游标协调之前以 `O_EXCL` 分配首个/空闲日志槽，修复旧实现约 101ms setup 即触发的间歇 CI 失败，完整窗口/压力语义不变且未做异步改造。该 pin 未重跑 native 验收；下述历史证据保留其实际测量的 0.5.3 版本。大日志命令/TaskOutput 使用核心有界头尾与完整原始远端引用；增量页保持游标无损。自动更新包保留 `toolTimeoutMs=660000` 和 `REMOTE_DEV_HARNESS_PROFILE=kimi`，并在 discovery 前绑定 profile。

bootstrap 与 active 的分离复用现有 setup/component_prepare 边界：候选 setup/CI 用 overrides 构建 active runtime，旧控制器首跳只解析 bootstrap；component_prepare 随后保留已安装的较新组件或选择更新的正式 release，不降级。0.5.4/4c 是 active candidate pin，正式发布前不声称已被正式 feed 发现。父级旧控制器证据（`parent-kimi-upgrade-0.1.2-6a14d4faa2`、`parent-kimi-upgrade-0.1.3-c45df72748`）在隔离 Kimi home 中完成旧 0.1.2/0.1.3 build_runtime -> component_prepare -> build_host_package -> 实际 switch/native_install；随后候选正常 updater 修复旧包，已注册前端返回 29 个工具，timeout `660000`、profile `kimi`、diagnostics `0.5.3`（该证据测于 0.5.3，本次未重跑，不重标为 0.5.4）。实际源码验收为 Kimi `b8dbc14`/core `1fefcc3`；fresh setup 的 `build_bootstrap_runtime` 也经 `parent-kimi-fresh-runtime-077fc3046a` 实测得到 0.5.3/1c465a18、knowledge 0.13.3/38a、core 1fef。

但是两次正常 updater check 均以 exit 1 结束，原因是正式 feed 返回 `no_compatible_release`/`skipped_undeclared_releases7`，同时 `host_package_repair` 已成功安装；未重放不确定写入。该证据证明候选迁移/修复/已加载前端，不等同于未发布 release 的正式发现在线或已分发。0.1.3 控制器带 overrides。

本地/候选 feed 验证不代表正式 GitHub 自动分发已完成。
