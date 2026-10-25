# mindie-agent-kimi 0.1.4

本版本固定 remote-dev `1fefcc38` 与 diagnostics `0.5.3`。大日志命令/TaskOutput 使用核心有界头尾与完整引用；增量页保持游标无损。自动更新包保留 `toolTimeoutMs=660000` 和 `REMOTE_DEV_HARNESS_PROFILE=kimi`，并在 discovery 前绑定 profile。

不可变的 0.1.2 控制器首跳没有 `--overrides`，无法直接解析 0.5.3 与 knowledge 的 801a01b 传递 URL；这是已记录的旧控制器边界，不是无缝自动迁移。恢复使用候选 `scripts/setup.py`（带 overrides）的一次性路径，已在私有 venv 验证最终 diagnostics 0.5.3 与 remote-dev `1fefcc38`。0.1.3 控制器带 overrides，路径不同。

本地/候选 feed 验证不代表正式 GitHub 自动分发已完成。
