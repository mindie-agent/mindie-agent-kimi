# mindie-agent-kimi 0.1.4

运行时使用 remote-dev 0.10.7（`4c40cb5`）与 diagnostics 0.5.4（`5ae643e`）。远端原生工具保留参数语义和真实退出状态；大日志返回有界头尾及完整远端日志引用，增量输出不跳过未交付字节。diagnostics 首次分配日志文件不再等待读者注册表，修复初始化超过 100ms 时丢失首条记录的问题；已有日志回收与压力控制保持原语义。

本版本兼容已发布的 0.1.2/0.1.3 更新器。不可变 0.1.2 更新器的无 overrides 首跳使用可共同解析的 bootstrap：diagnostics 0.5.2、remote-dev 0.10.6、knowledge 0.13.3；随后由既有组件准备流程保留已安装的较新组件或选择较新的正式 Release。新安装与候选构建通过 `runtime-active-overrides.txt` 选择 diagnostics 0.5.4 / remote-dev 0.10.7。发布顺序必须是 diagnostics → remote-dev → 插件。

隔离原生验收已使用真实 0.1.2 和 0.1.3 控制器，在当前组件已就绪的条件下完成构建、组件准备、原生安装与切换。实测 Kimi 源码为 `5282177`，core 为 `4c40cb5`，diagnostics 为 `5ae643e`；之后仅修订本发布说明。候选自动修复旧控制器生成的宿主包，已注册启动器回读到 29 个工具、`toolTimeoutMs=660000`、`REMOTE_DEV_HARNESS_PROFILE=kimi` 以及 diagnostics 0.5.4 / core 0.10.7。启动依赖的无 overrides 解析和三系统 CI 均通过。

该验收保留发布前正式源检查的 exit 1 / `no_compatible_release`，不将隔离候选迁移当作正式分发证明。正式发布后还需检查正常后台发现、安装和激活。安装绑定可自动切换；已运行宿主缓存的 Skill、命令和 MCP 定义在宿主自然重载或新进程时生效，活动会话和既有授权保持不变。
