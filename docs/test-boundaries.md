# Test boundaries and failure detection

The September Windows/WSL audit found green component tests alongside a broken
Windows MCP launch. Manifest registration and an inventory readback did not
exercise the host's Node `shell:false` launch. The generated `.cmd` entry failed
with `EINVAL`. CI also omitted Windows entirely.

The supported operating systems now run ordinary unittest jobs. Checks run in
this order inside the same job, so an early failure stops expensive work:

1. Verify installed commit pins and availability of the explicitly supplied peer
   fixture. Missing setup exits 2 instead of generating many product failures.
2. Execute the unchanged installed-runtime probe in a clean child interpreter.
   A negative control removes a consumed method and proves that the probe rejects
   it. The old test-only Admission compatibility shim has been removed.
3. On Windows/Linux, use Node to execute the generated MCP manifest and perform
   real initialization and tools/list against the installed runtime. Windows
   also exercises normal-exit and timeout descendant ownership with a watchdog.
4. Exercise real frontend cancellation, EOF, an unread large response, and a
   completely received large response. Windows now runs these cases as well.
5. Run the component suite for consent, scope, identity, publication, update
   rollback, diagnostics and other behavior.

## Independent failure dimensions

| Boundary | What the assertion observes | What may be replaced |
| --- | --- | --- |
| Dependency compatibility | Unmodified installed package and consumed API | Missing API only in the rejecting negative control |
| Manifest launch | Node actually starts the selected executable with exact arguments | The argument-echo child for quoting; a second case uses actual MCP modules |
| Process ownership | A real descendant is dead after its leader exits | Nothing in launch, Job assignment or liveness |
| Output pressure | Complete large response, or bounded exit when the peer stops reading | The remote tool's business payload |
| Concurrent configuration | Fields survive real kernel-lock contention | An observation wrapper signals contention without changing the lock result |
| Update rollback | Pointer/package state and removal of incomplete Git generations | Download/build and the native registration response in component tests |

No row proves every other row. A positive output case cannot establish timeout
cleanup, and a live-leader timeout cannot establish ownership after leader exit.
Windows liveness uses a waitable process handle; `os.kill(pid, 0)` is not a safe
observation on Windows. Permission fixtures deny the numeric current-user SID,
and test-owned diagnostics writers close before scratch removal.

The original frontend tests skipped Windows because their observer used POSIX
selectors. Porting the observer exposed a real unread-pipe shutdown hang. The
four transport tests now pass on the Windows host in about 1.5 seconds, without
a model or a network call. Genuine POSIX mechanism tests (flock, FIFO and process
groups) remain platform-specific, alongside Windows mechanism checks.

These checks complement native Kimi acceptance; they do not prove model selection,
native command provenance, or model-driven retrieval. The audit's K3 session was
blocked by an exhausted weekly quota. That lane remains unaccepted.

References: [Python pipe blocking support](https://docs.python.org/3.13/library/os.html#os.set_blocking),
[Windows pipe mode](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-setnamedpipehandlestate).
