"""[Role] Expose deterministic component CLIs through the native MCP boundary.

[Invariants]
- Component parsers and engines own semantics; argv is passed without a shell.
- Discovery imports no component, starts no service and reads no knowledge.
- Local diagnostics never starts a reporter or uploads a support bundle.

[Timeouts & Locks]
- One owned child, a caller deadline (120s by default), and at most 1 MiB output.
- The shared process runner owns cancellation and process-tree cleanup.

[Side-Effect Policy]
- Only explicit calls execute a component. Missing installations are errors.
- Optional component_commands in the adapter selects an installed executable
  and fixed arguments; it never installs or silently substitutes a runtime.
"""
import json
import os
from pathlib import Path
import shutil
import sys
import sysconfig

from bounded import CommandTimedOut, OutputLimitExceeded, run
from paths import config_path, load_adapter_config


DESCRIPTIONS = {
    "profiler": (
        "Inspect and analyze Ascend NPU profiling captures using mindie-profiler. "
        "Pass CLI arguments as an argv array, e.g. ['inventory', '--root', '/capture'] "
        "or ['analyze', '--root', '/capture', '--full-range']. Commands: inventory, analyze, "
        "correlate, compare, sql, camodel, and kernels. Use ['--help'] to inspect options "
        "and --output for large reports."
        " Offline analysis of existing captures requires no device lease or managed task binding."
    ),
    "sim": (
        "Compute Ascend hardware bounds (A2/A3/A5), operator/graph/serving latency, HBM capacity "
        "(including async_scheduling delayed release), Roofline efficiency, SLA concurrency "
        "frontiers, and P/D disaggregation using mindie-sim. Pass CLI arguments as an argv "
        "array: synthesize a case from model/hardware IDs or HF config (e.g. ['compose', "
        "'--model', 'qwen3.5-27b', '--hardware', 'a3', '--tp', '8', '--concurrency', '32', "
        "'--async-scheduling', '--output', '/case.json']) and run ['predict', '/case.json'], "
        "['efficiency', '/case.json'], ['sla', '/case.json'], ['sweep', '/case.json', "
        "'--mode', 'pd'], ['skew', '/case.json'], or ['graph', '/case.json', '--phase', "
        "'prefill', '--tokens', '32']. Use ['--help'] to inspect options and --output for "
        "large reports."
        " Simulation calculations require no device allocation or managed task binding."
    ),
    "coordinator": (
        "Manage Ascend host inventory, probe CPU/RAM/NPU capacity, bind containers, "
        "and acquire/inspect/release NPU and service-port leases through "
        "mindie-coordinator. Pass CLI argv, e.g. ['hosts', '--action', 'list'] "
        "or ['probe', '--json', '{\"action\":\"hosts\"}']. Use ['--help'] to inspect options."
        " Host inventory, probes, containers and bare-metal leases need no managed "
        "task binding or knowledge activation. Omit context_file for these independent "
        "operations; inspect resources before acquiring a lease."
    ),
    "debugger": (
        "Ascend NPU fault triage (`triage`, `decode-code`), ACLGraph/NPUGraph capture/replay & buffer-address "
        "debugger (`aclgraph`: `guard`, `inspect`), and operator precision/memory-stomp toolkit (`precision`: "
        "`check-call`, `replay-call`, `inspect-capsule`, `compare-traces`, `watch`) via mindie-debugger. "
        "Error-code decoding (`['decode-code', '507015']`) or inline stderr triage (`['triage', '--text', '<err>']`) "
        "runs locally. Since full CANN `plog` directories (`$ASCEND_PROCESS_LOG_PATH` or `~/ascend/log/debug/plog`) "
        "and dynamic ACLGraph/precision runtime hooks reside inside the remote NPU container, call `['remote-guide']` "
        "(or `['triage']` / `['aclgraph', ...]` / `['precision', ...]`) to stage the pure-Python `mindie_debugger` "
        "bundle locally and get the exact `remote-dev` (`remote_artifact_push` + `remote_bash`) commands to triage "
        "CANN `plog`s in-place (`python3 -m mindie_debugger.cli triage`) or run `ACLGraphCaptureGuard`, "
        "`ACLGraphAddressTracker`, `ACLGraphDumper`, `ACLGraphVllmInspector`, and `precision` inside the NPU container."
    ),
}


def catalog():
    """The public catalog contains only the deterministic Agent capabilities."""
    limits = {
        "cwd": dict(type="string", description="Absolute working directory. Defaults to the verified native session directory."),
        "timeout_ms": dict(type="integer", minimum=1, maximum=600000, default=120000,
                           description="Execution deadline up to 600s; native MCP allows 660s including dispatch and cleanup."),
        "max_output_tokens": dict(type="integer", minimum=1, maximum=262144, default=16384,
                                  description="Output byte budget divided by four. Use a component output file for larger reports."),
    }
    result = []
    for name, description in DESCRIPTIONS.items():
        result.append(dict(
            name=name, description=description + " No MindIE knowledge activation is required.",
            inputSchema=dict(type="object", additionalProperties=False, required=["argv"],
                             properties=dict(argv=dict(type="array", items=dict(type="string"),
                                                       description="Arguments for the installed CLI; no shell expansion."),
                                             **limits)),
            annotations=dict(readOnlyHint=False, destructiveHint=True, openWorldHint=True),
        ))
    for name, description, properties, required in (
        ("diagnostics_status", "Inspect local MindIE diagnostic/reporting status without starting a worker or contacting GitHub.",
         dict(config=dict(type="string", description="Optional existing diagnostics policy path.")), []),
        ("diagnostics_bundle", "Export a bounded, sanitized local diagnostic support bundle. Does not upload it.",
         dict(root=dict(type="string", description="Optional local diagnostics log root; defaults to the active MindIE diagnostics root."),
              operation_id=dict(type="string"), incident_id=dict(type="string", description="Incident identifier alias for operation_id."),
              output=dict(type="string", description="Local output file.")), []),
    ):
        result.append(dict(name=name, description=description,
                           inputSchema=dict(type="object", additionalProperties=False,
                                            properties=dict(properties, **limits), required=required),
                           annotations=dict(readOnlyHint=name == "diagnostics_status",
                                            destructiveHint=False, openWorldHint=False)))
    return result


def command(adapter, component):
    configured = adapter.get("component_commands", {}).get(component)
    if configured is not None:
        if (not isinstance(configured, list) or not configured
                or not all(isinstance(value, str) and value for value in configured)):
            raise ValueError("component_commands must contain nonempty executable argv arrays")
        return configured
    if component == "diagnostics":
        return [adapter["python"], "-I", "-m", "mindie_diagnostics.cli"]
    runtime = Path(adapter["python"])
    runtime_paths = [str(runtime.parent)]
    if runtime == Path(sys.executable):
        runtime_paths.append(sysconfig.get_path("scripts"))
    search_path = os.pathsep.join([*runtime_paths, os.environ.get("PATH", "")])
    executable = shutil.which("mindie-" + component, path=search_path)
    if executable is None:
        raise FileNotFoundError(
            "mindie-" + component + " is not installed on PATH; install the component "
            "or configure component_commands." + component + " in the adapter")
    return [executable]


def execute(name, arguments, session, *, cancel=None, timeout=None):
    adapter = load_adapter_config()
    profile = adapter.get("profile_config")
    if profile and Path(profile) != config_path():
        profile_value = json.loads(Path(profile).read_text(encoding="utf-8"))
        adapter["component_commands"] = profile_value.get("component_commands", {})
    if name in DESCRIPTIONS:
        component = name
        argv = arguments["argv"]
        if not isinstance(argv, list) or not all(isinstance(value, str) for value in argv):
            raise ValueError("argv must be an array of CLI arguments")
    elif name == "diagnostics_status":
        component, argv = "diagnostics", ["reporting", "status"]
        if arguments.get("config") is not None:
            argv += ["--config", arguments["config"]]
    elif name == "diagnostics_bundle":
        root_val = arguments.get("root")
        if root_val is None:
            explicit_root = os.environ.get("MINDIE_DIAGNOSTICS_ROOT")
            if explicit_root:
                root_val = str(Path(explicit_root).expanduser())
            elif os.name == "nt" and not os.environ.get("XDG_STATE_HOME") and os.environ.get("LOCALAPPDATA"):
                root_val = str(Path(os.environ["LOCALAPPDATA"]) / "mindie" / "diagnostics")
            elif os.environ.get("XDG_STATE_HOME"):
                root_val = str(Path(os.environ["XDG_STATE_HOME"]) / "mindie" / "diagnostics")
            else:
                root_val = str(Path.home() / ".local" / "state" / "mindie" / "diagnostics")
        component, argv = "diagnostics", ["bundle", "--root", root_val]
        op_id = arguments.get("operation_id") if arguments.get("operation_id") is not None else arguments.get("incident_id")
        if op_id is not None:
            argv += ["--operation-id", op_id]
        if arguments.get("output") is not None:
            argv += ["--output", arguments["output"]]
    else:
        raise ValueError("unknown deterministic component tool")
    budget = arguments.get("max_output_tokens", 16384)
    if type(budget) is not int or not 1 <= budget <= 262144:
        raise ValueError("max_output_tokens must be between 1 and 262144")
    milliseconds = arguments.get("timeout_ms", 120000)
    if type(milliseconds) is not int or not 1 <= milliseconds <= 600000:
        raise ValueError("timeout_ms must be between 1 and 600000")
    bound = milliseconds / 1000
    if timeout is not None:
        bound = min(bound, timeout)
    environment = dict(os.environ, MINDIE_KIMI_CONFIG=str(config_path()))
    environment.pop("PYTHONPATH", None)
    invocation_token = os.urandom(16).hex() if name in DESCRIPTIONS else None
    if invocation_token is not None:
        environment["MINDIE_DIAGNOSTICS_INVOCATION_TOKEN"] = invocation_token
    cwd = arguments.get("cwd")
    if cwd is None:
        from identity import session_cwd

        cwd = session_cwd(session)
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("cwd must be an absolute working directory")
    child = command(adapter, component) + argv
    interrupted = None
    try:
        output = run(child, "", timeout=bound,
                     max_output=budget * 4, cancel=cancel, env=environment,
                     cwd=cwd, check=False, capture_result=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Could not start {child[0]} in {cwd}: {exc}") from exc
    except (CommandTimedOut, OutputLimitExceeded) as exc:
        interrupted = exc
    if interrupted is None:
        stdout = output.stdout
        value = dict(component=component, exit_code=output.returncode,
                     stdout=stdout, stderr=output.stderr, status="completed")
    else:
        stdout = interrupted.mindie_output
        value = dict(
            component=component, exit_code=interrupted.returncode,
            stdout=stdout, stderr=interrupted.mindie_stderr,
            status="timeout" if isinstance(interrupted, CommandTimedOut) else "output_limit",
            error=str(interrupted), output_truncated=isinstance(interrupted, OutputLimitExceeded),
        )
    cleanup_error = getattr(interrupted or output, "cleanup_error", None)
    if cleanup_error:
        value.update(cleanup_status="failed", cleanup_error=cleanup_error)
    # JSON output is additive: keep the exact stdout and exit status even when
    # a component emits help text or a non-JSON failure.
    try:
        value["result"] = json.loads(stdout)
    except json.JSONDecodeError:
        pass
    if name in DESCRIPTIONS and interrupted is None and output.returncode not in (0, 2):
        import diagnostic_support
        diag = diagnostic_support.record_component_crash(
            name, argv, output.returncode, output.stderr,
            parsed_result=value.get("result"), executable=child[0],
            invocation_token=invocation_token,
        )
        if diag is not None:
            value["diagnostic"] = diag
    return dict(content=[dict(type="text", text=json.dumps(value, ensure_ascii=False))],
                structuredContent=value, isError=interrupted is not None or output.returncode != 0)
