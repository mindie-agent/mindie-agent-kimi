# mindie-agent-kimi 0.1.7

This release integrates persistent SSH authentication from remote-dev 0.11.1.
After a user supplies a password once, remote-dev verifies access and attempts
to install and verify a managed public key. Password-only endpoints reuse the
saved credential across new chats and independent plugin runtimes. A rejected
key alone is not treated as proof that the server has disabled public keys.

Remote-dev owns the shared `~/.mindie/ssh/credentials.json` file and profiles
scoped by host, port, and SSH user. The plugin adds no password store or OS
keychain access and does not request the computer-account password. Key repair
happens before business submission; uncertain business operations are not
automatically replayed. Authentication failures preserve safe recovery hints,
submission state, and separate key-maintenance facts.

The Kimi adapter retains native nonce binding. The immutable 0.1.2 controller bootstrap trio remains knowledge 0.13.3, remote-dev 0.10.6, and diagnostics 0.5.2; `runtime-active-overrides.txt` selects the active versions listed below. Bootstrap installation and active-runtime cutover remain distinct.

The active runtime pins are:

| Component | Version / tag | Source commit |
| --- | --- | --- |
| remote-dev | 0.11.1 / v0.11.1 | `2a750ec8c74d7f053410d48dd820aeb542e7d3b1` |
| knowledge | 0.13.3 / v0.13.3 | `38a42f6f778cfa555d8918fdcacd12cee4a181f5` |
| diagnostics | 0.5.4 / v0.5.4 | `5ae643e19223f2181ecdbaa3edaf464137c4805e` |

Existing resolver overrides select diagnostics 0.5.4 over older transitive
pins. Normal resolver installation was verified in a separate virtual
environment, including installed `direct_url.json` identities and actual MCP
`tools/list` discovery of `remote_auth` with `password_env` and no plaintext
`password` field. Coordinator and npu-top remain independently installed
components; manual knowledge activation stays scoped to the native session.

Real SSH acceptance passed all 19 cases with no failures or skips: macOS
(7 tests, 35.190 seconds), Windows Server 2025 with PowerShell 7.6.7 and
Python 3.12.10 (4 tests, 17.503 seconds), the same PowerShell runtime inheriting
Git Bash PATH (4 tests, 16.485 seconds), and WSL2 Ubuntu 24.04 with OpenSSH
9.6p1 (4 tests, 17.338 seconds). The Git Bash PATH case discovers
`C:\Program Files\Git\ucrt64\bin\git.EXE` and selects that installation's
`usr/bin/ssh.exe`. [Platform run 40777028005](https://github.com/mindie-agent/remote-dev/actions/runs/40777028005)
tested remote-dev `f3592ffdef033aecb3d24b027dbd889204b65b28`, which has the
same source tree as the pinned 0.11.1 commit
(`8443a5d09f2b1a5d6f52d9637251b386af85846e`).

Coverage includes managed-key installation, reuse across independent virtual
environments, password-only authentication, revoked-key repair before one
business execution, and one shared credential used by coordinator, two Kimi
chat processes, and npu-top after reopening its database. Kimi uses its actual
nonce/MCP adapter; Codex, Claude Code and DSH use their shared endpoint profiles.
These checks do not claim complete native-host end-to-end runs or that an
existing host has loaded this release. All acceptance uses isolated state and
the default credential-file provider. Formal tag workflows attach test and
benchmark reports plus checksums from the exact released source.
