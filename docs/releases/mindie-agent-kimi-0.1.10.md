# mindie-agent-kimi 0.1.10

This release repairs a runtime downgrade found during the formal 0.1.9 native
upgrade acceptance. It preserves the task-information-based knowledge routing
and standard component command binding changes from 0.1.9.

## Failure and correction

The observed 0.1.8 updater installed the immutable bootstrap requirements while
knowledge 0.13.6 interpreted the candidate's active overrides as that build's
baseline. When selected component SHAs matched the declaration, the updater
skipped the managed runtime build without inspecting the candidate interpreter.
The native plugin advanced to 0.1.9, but its interpreter regressed from
knowledge 0.13.6 / remote-dev 0.11.1 / diagnostics 0.5.4 to the bootstrap trio
0.13.3 / 0.10.6 / 0.5.2. Later scheduled checks exited because the bootstrap
knowledge package lacks the component binding API required by 0.1.9.

Version 0.1.10 separates the two contracts:

- `runtime-requirements.txt` retains the consistent bootstrap dependency pins
  consumed by published older controllers.
- Active source requirements move to `runtime/active-overrides.txt`; the old
  top-level filename is absent, so even an older controller using knowledge
  0.13.6 sees the bootstrap baseline it actually installs.
- New code explicitly requests the active source floor from knowledge 0.13.7
  and compares the candidate interpreter's actual inventory with the selected
  target. A matching declaration alone cannot skip the required runtime build.
- The existing formal-release selection, installed-version floors, immutable
  runtime build and idle native switch remain authoritative.

The active requirements select knowledge 0.13.7, remote-dev 0.11.1 and
diagnostics 0.5.4. Their exact source revisions are recorded in
`runtime/active-overrides.txt`.

## Existing 0.1.9 installations

An installation already left on the bootstrap trio by the 0.1.9 hop needs one
explicit recovery. Publishing a newer plugin cannot repair a controller whose
required import fails before it can prepare that plugin. From the verified
0.1.10 release source directory, use the existing recovery entry:

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r runtime-requirements.txt --overrides runtime/active-overrides.txt
.venv/bin/python scripts/updater.py recover --config "$HOME/.config/mindie-agent/kimi.json"
```

Use the original configuration path for a custom profile and
`.venv\Scripts\python.exe` on Windows. This creates a separate recovery
environment and uses the product's normal native installation and rollback
transaction. It does not hand-edit the active binding, remove previous
generations or reset consent.

Recovery of an already damaged installation and an automatic first hop from an
undamaged older installation are different acceptance cases. Both require
actual interpreter/version readback; subsequent scheduled checks must keep the
correct components active. Existing host Skill and command caches still
refresh at a natural host reload or new process.

## Verification scope

Regression contracts cover a bootstrap-only candidate whose declared target
SHAs already match the selected release, both during plugin staging and after
activation. Setup, candidate builds and CI consume the relocated active
requirements. The release retains support for Kimi Code 2.1.1, 2.2.0 and 2.3.1,
and the exact-tag release pipeline publishes source, compatibility metadata,
checksums and required CPU benchmark reports.

These tests and microbenchmarks do not establish a global task-speed benefit.
The failed 0.1.9 upgrade evidence remains a failure; later recovery does not
retroactively turn it into a successful automatic upgrade.
