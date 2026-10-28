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

from bounded import run
from paths import config_path, load_adapter_config


DESCRIPTIONS = {
    "profiler": (
        "Inspect and analyze Ascend profiling captures using mindie-profiler. "
        "Pass CLI arguments as an argv array, e.g. ['inventory', '--root', '/capture'] "
        "or ['analyze', '--root', '/capture', '--full-range']. Also supports "
        "correlate, compare, sql, camodel and kernels; use ['--help'] or a "
        "subcommand's --help for the installed parser. Use --output for large "
        "reports. Device capture still requires an appropriate NPU lease."
    ),
    "sim": (
        "Run explicit-input performance simulations using mindie-sim. Pass CLI "
        "arguments as an argv array; use ['--help'] and subcommand --help for the "
        "installed version's supported models, operators, graphs and serving "
        "calculations. Use --output for large reports when supported. "
        "A partial calculation keeps its report with a nonzero exit. "
        "Output-file calls may have empty stdout; the exit code and stderr remain "
        "available. Predictions do not establish measured hardware accuracy. "
        "Check CPU/RAM needs before large runs and use remote-dev "
        "on a suitable host when local resources are insufficient."
    ),
    "coordinator": (
        "Manage Ascend host inventory, probe CPU/RAM/NPU capacity, bind containers "
        "and acquire/inspect/release NPU and service-port leases through "
        "mindie-coordinator. Pass CLI argv, e.g. ['hosts', '--action', 'list'] "
        "or ['probe', '--json', '{\"action\":\"hosts\"}']. Use ['--help'] "
        "for the installed parser. Preserve lease IDs and uncertain external "
        "outcomes; never resubmit an uncertain mutation."
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
         dict(root=dict(type="string", description="Local diagnostics log root."),
              operation_id=dict(type="string"), output=dict(type="string", description="Local output file.")), ["root"]),
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
    search_path = os.pathsep.join([
        str(Path(adapter["python"]).parent),
        os.environ.get("PATH", ""),
    ])
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
        component, argv = "diagnostics", ["bundle", "--root", arguments["root"]]
        for key in ("operation_id", "output"):
            if arguments.get(key) is not None:
                argv += ["--" + key.replace("_", "-"), arguments[key]]
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
    cwd = arguments.get("cwd")
    if cwd is None:
        from identity import session_cwd

        cwd = session_cwd(session)
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise ValueError("cwd must be an absolute working directory")
    child = command(adapter, component) + argv
    try:
        output = run(child, "", timeout=bound,
                     max_output=budget * 4, cancel=cancel, env=environment,
                     cwd=cwd, check=False, capture_result=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Could not start {child[0]} in {cwd}: {exc}") from exc
    stdout = output.stdout
    value = dict(component=component, exit_code=output.returncode,
                 stdout=stdout, stderr=output.stderr)
    if getattr(output, "cleanup_error", None):
        value.update(cleanup_status="failed", cleanup_error=output.cleanup_error)
    # JSON output is additive: keep the exact stdout and exit status even when
    # a component emits help text or a non-JSON failure.
    try:
        value["result"] = json.loads(stdout)
    except json.JSONDecodeError:
        pass
    return dict(content=[dict(type="text", text=json.dumps(value, ensure_ascii=False))],
                structuredContent=value, isError=output.returncode != 0)
