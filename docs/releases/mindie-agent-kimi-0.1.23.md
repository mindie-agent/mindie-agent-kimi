# mindie-agent-kimi 0.1.23

[English](mindie-agent-kimi-0.1.23.en.md)

- **更新与组件目录传输层 TLS/HTTP 异常分类对齐（`bounded.py`、`component_update.py`、`updater.py`）**：将响应流读取或握手阶段直接抛出的瞬时 `ssl.SSLError`（区别于 `OSError` 子树中的 `ConnectionError`）、`http.client.HTTPException`（排除本地编程错误 `InvalidURL` / `UnimplementedFileMode`）、`socket.gaierror`/`socket.herror` 及子进程 TLS 连接中断归类为 `temporary_network`（`connectivity`，`reportable=False`），同时将 `ssl.SSLCertVerificationError`（含 `URLError` 包装场景）精确归类为 `certificate`（`credentials` / `action-required`），消除网络抖动触发误报内部故障单（`mindie-agent#350`）。
