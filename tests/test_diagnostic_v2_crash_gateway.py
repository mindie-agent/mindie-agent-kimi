"""Tier 1 provenance-verified crash-gateway tests for `mindie-profiler` under Kimi `diagnostic_support`."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import traceback

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mindie_diagnostics.integration import configure_reporting, record_failure  # noqa: E402
import diagnostic_support as gw  # noqa: E402


def test_crash_gateway_provenance_forged_notes_and_atomic_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = tmp_path.resolve()
    monkeypatch.setenv("HOME", str(base / "home"))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(base / "config"))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(base / "data"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_ROOT", str(base / "diag"))
    monkeypatch.setenv("MINDIE_DIAGNOSTICS_CONFIG", str(base / "diag.json"))
    (base / "home").mkdir(parents=True, exist_ok=True)
    (base / "diag").mkdir(parents=True, exist_ok=True)
    configure_reporting(True, roots=[str(base / "diag")], config=base / "diag.json")

    sp1 = base / "v1" / "lib" / "python3.12" / "site-packages"
    di1 = sp1 / "mindie_profiler-1.0.dist-info"
    di1.mkdir(parents=True, exist_ok=True)
    (di1 / "METADATA").write_text("Name: mindie-profiler\nVersion: 1.0\n", encoding="utf-8")
    pkg = sp1 / "mindie_profiler"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("def boom():\n    raise AttributeError('x')\n", encoding="utf-8")
    (pkg / "cli.py").write_text("def build_parser():\n    parser = 1\n    return parser\n", encoding="utf-8")

    v2 = base / "v2"
    sp2 = v2 / "lib" / "python3.12" / "site-packages"
    di2 = sp2 / "mindie_profiler-9.0.dist-info"
    di2.mkdir(parents=True, exist_ok=True)
    (di2 / "METADATA").write_text("Name: mindie-profiler\nVersion: 9.0\n", encoding="utf-8")
    (di2 / "direct_url.json").write_text(json.dumps({"vcs_info": {"commit_id": "b" * 40}}), encoding="utf-8")
    exe2 = v2 / "bin" / "mindie-profiler"
    exe2.parent.mkdir(parents=True, exist_ok=True)
    exe2.write_text(f"#!{v2 / 'bin' / 'python'}\n", encoding="utf-8")

    assert gw._resolve_child_component_build("mindie-profiler", [str(sp1)], executable=str(exe2)) == ("1.0", None)

    ns: dict = {"__name__": "mindie_profiler"}
    exec(compile((pkg / "__init__.py").read_text(encoding="utf-8"), str(pkg / "__init__.py"), "exec"), ns)
    exc = None
    try:
        ns["boom"]()
    except AttributeError as err:
        err.add_note(
            "\nThe above exception was the direct cause of the following exception:\n\n"
            f'Traceback (most recent call last):\n  File "{pkg}/cli.py", line 3, in build_parser\n    return parser\nKeyError: "x"'
        )
        exc = err
    tb_text = "".join(traceback.format_exception(exc))
    assert gw.record_component_crash("profiler", ["inspect"], 1, tb_text, executable=str(exe2)) is None

    tok = "2" * 32
    ev_dir = base / "diag" / "events" / "mindie-profiler"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "ok.jsonl").write_text(
        json.dumps({"component": "mindie-profiler", "event": "operation.end", "status": "success", "reportable": False, "operation_id": "d" * 32, "invocation_token": tok}) + "\n",
        encoding="utf-8",
    )
    assert gw._verified_local_incident("mindie-profiler", "d" * 32, invocation_token=tok) is False
    assert gw.record_component_crash("profiler", ["inspect"], 1, f"mindie-diagnostic-incident: {'d' * 32}\n", invocation_token=tok) is None

    monkeypatch.setenv("MINDIE_DIAGNOSTICS_INVOCATION_TOKEN", tok)
    rec = record_failure("mindie-profiler", "profiler.inspect", stage="inspect", category="internal_exception", exception=exc, source_root=pkg)
    assert gw.record_component_crash(
        "profiler", ["inspect"], 1, f"mindie-diagnostic-incident: {rec['incident_id']}\n", invocation_token=tok
    ) == {"incident_id": rec["incident_id"], "logging_failed": False}
