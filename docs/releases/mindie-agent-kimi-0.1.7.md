# mindie-agent-kimi 0.1.7

Release preparation is in progress. The remote-dev 0.11.0 formal dependency
revision and final platform evidence must be recorded before this candidate is
published.

The active runtime integrates remote-dev's shared SSH authentication. One
password entry verifies access and attempts to install and verify a managed
public key. Password-only endpoints can reuse their saved credential across new
Kimi chats, separate runtimes, coordinator access, and monitor probes under the
same local user.

Credentials belong to remote-dev, with the default file at
`~/.mindie/ssh/credentials.json`. References are scoped by SSH host, port, and
user. The Kimi adapter keeps its native nonce binding and adds no password store,
OS keychain access, or computer-account password prompt.

The old 0.1.2 controller's bootstrap trio in `runtime-requirements.txt` remains
knowledge 0.13.3, remote-dev 0.10.6, and diagnostics 0.5.2. The active candidate
selects its new remote-dev revision and diagnostics 0.5.4 through
`runtime-active-overrides.txt`. Bootstrap completion and active-runtime cutover
are distinct states; an existing newer component must not be downgraded.

Authentication acceptance includes the actual Kimi nonce/MCP adapter in two
fresh chat processes with installed runtime packages. This is adapter coverage,
not a claim of an LLM-driven Kimi host update. The exact source revisions and
platform results must accompany the final evidence. Formal release assets must
include the final tag's test and benchmark reports and checksums.
