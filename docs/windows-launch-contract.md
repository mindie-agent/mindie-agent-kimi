# Native launch validation

Kimi 0.42.0 accepts PATH command tokens or package-relative `./` commands in
plugin MCP declarations. Acceptance by that schema and a successful plugin
inventory record do not prove that the command can be spawned.

Windows stdio MCP uses a process without a shell. The former
`./mindie-front.cmd` passed registration but failed with `spawn EINVAL`.
Windows packages now declare the interpreter's executable basename with its
retained directory first in the server's child `PATH`, and pass the launcher,
configuration and operation as separate arguments. POSIX packages retain the
executable shell wrapper. No global PATH or user profile is modified.

Run the native boundary lane after installing `runtime-requirements.txt`, with
Node.js on PATH:

```sh
python -m unittest discover -s tests/native -v
```

The two checks have distinct responsibilities:

- Generated manifest arguments execute through real Node `spawnSync` with
  `shell: false`, in paths containing Unicode, spaces and shell metacharacters.
  A real Python child exposes its executable and received arguments.
- Generated launchers complete a live MCP initialization and `tools/list`
  exchange through the selected runtime for both knowledge and remote
  surfaces. No dependency API, process or MCP response is mocked.

On the Windows validation machine, these checks took about 1.1 seconds.
The same argument test against the original `281fd795` updater failed with
`EINVAL` for both surfaces; the candidate passed. The candidate also installed
through Kimi's native plugin API, and its installed package returned four
knowledge tools and eighteen remote tools through a real MCP transport.

These results establish registration and process/protocol contracts, not
model-driven task completion. The attempted K3/max revalidation was blocked by
the account's weekly quota (HTTP 403); it must be repeated when quota is
available. Read-only feed synchronization is separate: isolated installs using
`--no-schedule` need an explicit sync/update check before retrieval acceptance.

The same native lane also checks Windows descendant ownership after the leader
exits, for normal exit and inherited-pipe timeout. The stdlib front uses a
suspended start, establishes a Windows Job, and then resumes execution. Late
Job assignment was reproduced as a race; a Job must exist before execution.
The initial 0.5-second timeout needed external cleanup after four seconds;
the repaired probe returned in 0.704 seconds. All three native tests passed
in 2.084 seconds, including both process terminal states.

Host references: [plugin command rules](https://moonshotai.github.io/kimi-code/en/customization/plugins.html)
and [MCP child environment](https://moonshotai.github.io/kimi-code/en/customization/mcp).
