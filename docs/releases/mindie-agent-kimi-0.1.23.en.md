# mindie-agent-kimi 0.1.23

[中文](mindie-agent-kimi-0.1.23.md)

- **Updater & Component Catalog TLS/HTTP Transport Classification (`bounded.py`, `component_update.py`, `updater.py`)**: Classifies transient mid-stream or handshake `ssl.SSLError` (sibling to `ConnectionError` under `OSError`), `http.client.HTTPException` (excluding local `InvalidURL` / `UnimplementedFileMode` bugs), `socket.gaierror`/`socket.herror`, and child-process TLS reset output as `temporary_network` (`connectivity`, `reportable=False`), while distinguishing `ssl.SSLCertVerificationError` (direct or wrapped in `URLError`) as `certificate` (`credentials` / `action-required`), preventing false internal fault reports during transient TLS resets (`mindie-agent#350`).
