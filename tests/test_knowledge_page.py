"""Schema and immutable-block wire. No native host is attached.

A call without a locally published PreToolUse bind fails closed. Real current
material packages exercise navigation and exact block reads through the
adapter's duplicated text/structured-content response.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, env_for, make_config

SESSION = "ses_page"

HELPER = r"""
import json, os, sys
sys.path.insert(0, os.environ["SCRIPTS"])
os.environ["MINDIE_KIMI_CONFIG"] = os.environ["CONFIG"]
import mcp_server

page = json.loads(open(os.environ["PAGE"], encoding="utf-8").read())

def knowledge_call(name, args, session):
    if name != "knowledge_explain":
        raise AssertionError(name)
    if session != os.environ["SESSION"]:
        raise AssertionError(session)
    if args.get("ref") != os.environ["REF"]:
        raise AssertionError("ref")
    if set(args) != {"ref"}:
        raise AssertionError("unexpected block-read arguments")
    if "request_nonce" in args or "session_id" in args or "_session_id" in args:
        raise AssertionError("identity leaked into the knowledge call")
    return page

mcp_server.knowledge_call = knowledge_call
response = mcp_server.handle("knowledge", json.loads(sys.stdin.read()))
if response is None:
    raise SystemExit("helper returned no response")
mcp_server.send(response)
"""

STUB = r"""
import json, sys
message = json.loads(sys.stdin.read())
blob = "测" * 88000
body = {
    "jsonrpc": "2.0",
    "id": message["id"],
    "result": {"content": [{"type": "text", "text": blob}], "isError": False},
}
sys.stdout.buffer.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
"""


def _bodies():
    from mindie_knowledge.materials.store import MAX_BLOCK_BYTES
    return {
        "ascii": "A" * MAX_BLOCK_BYTES,
        "chinese": "测" * (MAX_BLOCK_BYTES // 3),
        "nonbmp": "\U00020000" * (MAX_BLOCK_BYTES // 4),
        "escape": '\\"' * (MAX_BLOCK_BYTES // 2),
    }


def real_explore_result(method="query", domain="vllm-ascend"):
    """One real `mode="explore"` payload from the installed knowledge engine.

    Explore stripping, foundation promotion and the subagent fact pack are the
    engine's contract; adapter tests assert against its actual output rather
    than a hand-written response that could drift from the real schema.
    """
    from mindie_knowledge.graded.index import RoutingIndex
    from mindie_knowledge.graded.units import GradedUnit

    observed = {"soc": "Ascend910B"}

    def graded_unit(ident, tier, category, body, parents=(), strength="single_observation"):
        return GradedUnit.from_parts(
            {
                "schema": "mindie-graded-unit/1", "id": ident, "tier": tier,
                "category": category, "evidence_strength": strength,
                "domain_path": "serving/graph", "inherits_from": list(parents),
                "title": f"{ident} probe",
                "triggers": {"error_signatures": [], "keywords": ["rmsnorm"]},
                "conditions": {"soc": ["Ascend910B"]},
                "provenance": {"source_type": "session_distill",
                               "source_locator": "fixture@abc:probe.txt",
                               "source_digest": "0" * 64,
                               "evidence_refs": ["fixture"], "supersedes": []},
            },
            body,
            f"serving/graph/{ident}.md",
        )

    units = [
        graded_unit("unit-l1-foundation", "L1", "axiom", "Keep 32B UB alignment.",
                    strength="hardware_spec"),
        graded_unit("unit-l3-workflow", "L3", "workflow", "Old rmsnorm workflow steps.",
                    parents=["unit-l1-foundation"]),
        graded_unit("unit-l4-mutator", "L4", "mutator", "Fuse the rmsnorm epilogue."),
    ]
    index = RoutingIndex(units)
    options = dict(
        conditions=observed, mode="explore", token_budget=16000,
        native_subcontext=True, goal="Fuse RMSNorm epilogue",
        ledger_entries=[{"run_id": "r1", "verdict": "refuted", "hypothesis": "tile=64",
                         "conditions": observed, "axes": {"evidence_validity": "valid"}}],
    )
    if method == "explain":
        return index.explain_closure(
            ["mindie://vllm-ascend/units/unit-l3-workflow", "mindie://vllm-ascend/units/unit-l4-mutator"],
            domain=domain, **options)
    return index.query("rmsnorm", **options)


class KnowledgePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.config = make_config(self.tmp)
        self.diag = self.tmp / "diag"
        self.diag.mkdir()
        (self.diag / "logs").mkdir()
        (self.diag / "off.json").write_text('{"decision":"disabled"}\n')
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "MINDIE_KIMI_CONFIG",
                "MINDIE_DIAGNOSTICS_CONFIG",
                "MINDIE_DIAGNOSTICS_ROOT",
            )
        }
        os.environ["MINDIE_KIMI_CONFIG"] = str(self.config)
        os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(self.diag / "off.json")
        os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(self.diag / "logs")
        sys.path.insert(0, str(SCRIPTS))

    def tearDown(self):
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_explain_block_schema_and_query_continuation(self):
        import mcp_server

        explain = next(item for item in mcp_server.KNOWLEDGE_TOOLS if item["name"] == "knowledge_explain")
        query = next(item for item in mcp_server.KNOWLEDGE_TOOLS if item["name"] == "knowledge_query")
        feedback = next(item for item in mcp_server.KNOWLEDGE_TOOLS if item["name"] == "knowledge_feedback")
        self.assertEqual(
            set(explain["inputSchema"]["properties"]),
            {
                "ref", "refs", "conditions", "known_refs", "token_budget",
                "run_id", "mode", "ledger_entries", "bottleneck_summary",
                "native_subcontext", "goal", "request_nonce",
            },
        )
        self.assertEqual(explain["inputSchema"]["required"], ["request_nonce"])
        self.assertIn("next_block_ref", explain["description"])
        self.assertIn("slice", explain["description"])
        self.assertNotIn("offset", query["inputSchema"]["properties"])
        self.assertEqual(query["inputSchema"]["properties"]["limit"]["maximum"], 20)
        self.assertEqual(
            set(query["inputSchema"]["properties"]),
            {
                "query", "limit", "conditions", "continuation", "domain",
                "domain_path", "token_budget", "include_unknown", "include_archived",
                "navigation_only", "run_id", "mode", "ledger_entries",
                "bottleneck_summary", "native_subcontext", "goal", "request_nonce",
            },
        )
        self.assertEqual(
            set(feedback["inputSchema"]["properties"]),
            {
                "ref", "rating", "reason", "event_type", "run_id", "step_id",
                "outcome", "evidence_source", "request_nonce",
            },
        )
        self.assertEqual(feedback["inputSchema"]["required"], ["ref", "request_nonce"])
        distill = next(item for item in mcp_server.KNOWLEDGE_TOOLS if item["name"] == "knowledge_distill")
        self.assertEqual(
            set(distill["inputSchema"]["properties"]),
            {"inputs", "task_state", "related_refs", "proposal", "domain", "request_nonce"},
        )
        self.assertEqual(
            distill["inputSchema"]["required"],
            ["inputs", "task_state", "request_nonce"],
        )
        self.assertEqual(
            set(distill["inputSchema"]["properties"]["inputs"]["items"]["properties"]),
            {
                "kind", "outcome", "source_locator", "content",
                "evidence_refs", "repetitions",
            },
        )

    def test_query_rejects_invalid_arguments_before_starting_service(self):
        import mcp_server
        from unittest.mock import patch

        for invalid in ({}, {"query": "x", "continuation": "opaque-next-page"},
                        {"continuation": "malformed"}, {"query": "x", "limit": True}):
            with self.subTest(invalid=invalid), \
                    patch("knowledge_service.ensure_service") as start, \
                    patch("mindie_knowledge.loop.cli.rpc") as rpc:
                with self.assertRaises(ValueError):
                    mcp_server.knowledge_call("knowledge_query", invalid, SESSION)
                start.assert_not_called()
                rpc.assert_not_called()

    def test_query_does_not_time_out_a_cold_native_read(self):
        import admission
        import mcp_server
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, HTTPServer
        from unittest.mock import patch

        admission.activate(SESSION, project_root=str(self.tmp.resolve()))
        result = {"results": [{"ref": "synthetic-result"}]}
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                time.sleep(5.25)
                body = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            server.timeout = 10
            worker = threading.Thread(target=server.handle_request)
            worker.start()
            connection = {"url": f"http://127.0.0.1:{server.server_port}", "token": "synthetic"}
            try:
                with patch("knowledge_service.ensure_service", return_value=connection):
                    self.assertEqual(
                        mcp_server.knowledge_call("knowledge_query", {"query": "synthetic"}, SESSION),
                        result,
                    )
            finally:
                worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
        self.assertEqual(len(calls), 1)

    def test_explain_without_host_bind_fails_closed(self):
        message = {
            "jsonrpc": "2.0",
            "id": "no-host",
            "method": "tools/call",
            "params": {
                "name": "knowledge_explain",
                "arguments": {
                    "ref": "mindie://vllm-ascend/missing",
                    "request_nonce": "nonce-missing-host",
                },
            },
        }
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "mcp_server.py"), "knowledge", "--once"],
            input=(json.dumps(message) + "\n").encode(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env_for(self.config),
            timeout=15,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())
        response = json.loads(proc.stdout.decode())
        text = response["result"]["content"][0]["text"]
        self.assertTrue(response["result"]["isError"])
        self.assertIn("host did not authorize", text)
        self.assertNotIn("content_offset", text)

    def test_store_navigation_and_blocks_fit_knowledge_bound(self):
        import bounded
        import mindie_launch
        from mindie_knowledge.loop.store import Store
        from mindie_knowledge.materials import MaterialStore
        from mindie_knowledge.materials.references import task_ref

        knowledge_bound = mindie_launch.KNOWLEDGE_MAX_OUTPUT

        helper = self.tmp / "helper.py"
        helper.write_text(HELPER, encoding="utf-8")
        store = Store(self.tmp / "store", "vllm-ascend")
        author = MaterialStore(self.tmp / "author", "vllm-ascend")
        sizes = {}
        try:
            for name, body in _bodies().items():
                task = hashlib.sha256(name.encode()).hexdigest()
                second = "Later correction; reference material only.\n"
                author.append_batch(
                    task,
                    [dict(block_id=hashlib.sha256(f"{name}-{i}".encode()).hexdigest(),
                          text=text, source_range=dict(part=i),
                          title=f"Observation {i}", summary="Synthetic component fixture.")
                     for i, text in enumerate((body, second))],
                    "Two synthetic observations.", title="Local block fixture",
                    domain="vllm-ascend", status="complete", promote=True,
                )
                store.install_feed([author.export_task(task)], feed_ident="f" * 64)
                ref = task_ref("vllm-ascend", task)
                previous = None
                for position in range(3):
                    page = store.explain(ref)
                    out = self._helper(bounded, helper, name, ref, position, page)
                    response = json.loads(out)
                    result = response["result"]
                    self.assertIs(result["isError"], False)
                    text = json.loads(result["content"][0]["text"])
                    structured = result["structuredContent"]
                    self.assertEqual(text, page)
                    self.assertEqual(structured, page)
                    self.assertEqual(structured["ref"], ref)
                    nbytes = len(out.encode())
                    self.assertLessEqual(nbytes, knowledge_bound, f"{name} {nbytes}")
                    self.assertNotIn("next_offset", structured)
                    if position == 0:
                        self.assertEqual(structured["kind"], "task")
                        self.assertNotIn("content", structured)
                        ref = structured["first_block_ref"]
                    else:
                        self.assertEqual(structured["kind"], "block")
                        self.assertEqual(structured["content"], body if position == 1 else second)
                        self.assertEqual(structured["previous_block_ref"], previous)
                        previous, ref = ref, structured["next_block_ref"]
                        if position == 1:
                            sizes[name] = nbytes
                self.assertIsNone(ref)
        finally:
            store.close()
            author.close()
        self.assertEqual(set(sizes), set(_bodies()))

    def _helper(self, bounded, helper, name, ref, position, page):
        import identity
        import mindie_launch

        path = self.tmp / f"{name}-{position}.json"
        path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        nonce = f"nonce-{name}-{position}"
        arguments = {"ref": ref, "request_nonce": nonce}
        identity.publish_nonce_bind(
            SESSION, nonce, "knowledge_explain", f"tool-{name}-{position}", arguments
        )
        message = {
            "jsonrpc": "2.0",
            "id": f"id-{name}-{position}",
            "method": "tools/call",
            "params": {"name": "knowledge_explain", "arguments": arguments},
        }
        env = env_for(
            self.config,
            extra={
                "SCRIPTS": str(SCRIPTS),
                "CONFIG": str(self.config),
                "PAGE": str(path),
                "REF": ref,
                "SESSION": SESSION,
                # The real MCP writer uses UTF-8 bytes, independent of locale.
                "PYTHONIOENCODING": "cp1252",
                "MINDIE_DIAGNOSTICS_CONFIG": os.environ["MINDIE_DIAGNOSTICS_CONFIG"],
                "MINDIE_DIAGNOSTICS_ROOT": os.environ["MINDIE_DIAGNOSTICS_ROOT"],
            },
        )
        return bounded.run(
            [sys.executable, str(helper)],
            json.dumps(message),
            timeout=30,
            max_output=mindie_launch.KNOWLEDGE_MAX_OUTPUT,
            env=env,
        )

    def test_stub_is_written_as_utf8_regardless_of_locale(self):
        stub = self.tmp / "stub.py"
        stub.write_text(STUB, encoding="utf-8")
        # A cp936 default must not land: the non-ASCII blob is valid UTF-8.
        self.assertIn("测", stub.read_bytes().decode("utf-8"))

    def test_dispatch_raises_knowledge_bound_only(self):
        import bounded
        import mindie_launch
        from unittest.mock import patch

        generation = self.tmp / "generation"
        scripts = generation / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "mcp_server.py").write_text(STUB, encoding="utf-8")
        state = Path(json.loads(self.config.read_text())["state_dir"])
        current = state / "update" / "current.json"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_text(json.dumps({
            "generation": str(generation),
            "python": sys.executable,
            "adapter_config": str(self.config),
            "sha": "b" * 40,
        }))
        raw = json.dumps({"jsonrpc": "2.0", "id": "cap-1", "method": "tools/list"}).encode()
        with patch.dict(os.environ, {"PYTHONIOENCODING": "cp1252"}):
            response = mindie_launch._dispatch("knowledge", raw, "cap-1")
        text = response["result"]["content"][0]["text"]
        self.assertGreater(len(text.encode()), 256 * 1024)
        self.assertLess(len(text.encode()), mindie_launch.KNOWLEDGE_MAX_OUTPUT)
        self.assertEqual(bounded.MAX_OUTPUT, 256 * 1024)
        # The 32 MiB remote cap is separate from the local bound; the same
        # >256 KiB helper response must survive a remote dispatch.
        remote = mindie_launch._dispatch("remote", raw, "cap-1")
        self.assertGreater(len(remote["result"]["content"][0]["text"].encode()),
                           256 * 1024)

    def test_knowledge_bound_accepts_output_past_512kib(self):
        import bounded
        import mindie_launch

        self.assertEqual(mindie_launch.KNOWLEDGE_MAX_OUTPUT, 1024 * 1024)
        self.assertEqual(bounded.MAX_OUTPUT, 256 * 1024)
        payload = "x" * (512 * 1024 + 1)
        bounded.run(
            [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
            payload,
            timeout=10,
            max_output=mindie_launch.KNOWLEDGE_MAX_OUTPUT,
        )
        with self.assertRaises(bounded.OutputLimitExceeded):
            bounded.run(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                payload,
                timeout=10,
                max_output=512 * 1024,
            )

    def test_rsi_explore_query_and_explain_deliver_the_engine_result_verbatim(self):
        import admission
        import mcp_server
        import rsi_explore
        from unittest.mock import patch

        # Argument shaping is this adapter's only RSI responsibility: reject an
        # unknown mode before any service/store call, forward the selectors.
        with self.assertRaises(ValueError):
            rsi_explore.forwarded({"query": "rmsnorm", "mode": "invalid"})
        args = rsi_explore.forwarded({
            "query": "rmsnorm", "mode": "explore", "native_subcontext": True,
            "goal": "Fuse RMSNorm epilogue",
            "ledger_entries": [{"run_id": "r1", "verdict": "refuted", "hypothesis": "tile=64"}],
        })
        self.assertEqual(args["mode"], "explore")
        self.assertIs(args["native_subcontext"], True)

        # Explore stripping, foundation promotion and the subagent fact pack are
        # the knowledge engine's contract. A real local lease lets the adapter
        # run its own admission and engine selection; only the RPC boundary is
        # mocked, returning the genuine RoutingIndex output. The adapter must
        # deliver that result verbatim, with no second L3 filter or rebuilt pack.
        admission.activate(SESSION, project_root=str(self.tmp.resolve()))

        explain_args = rsi_explore.forwarded({
            "refs": ["mindie://vllm-ascend/units/unit-l3-workflow", "mindie://vllm-ascend/units/unit-l4-mutator"], "mode": "explore",
            "native_subcontext": True, "goal": "Fuse RMSNorm epilogue",
            "conditions": {"soc": "Ascend910B"},
            "ledger_entries": [{"run_id": "r1", "verdict": "refuted", "hypothesis": "tile=64"}],
        })
        for tool, call_args in (("knowledge_query", args),
                                ("knowledge_explain", explain_args)):
            engine_result = real_explore_result(tool.removeprefix("knowledge_"))
            served = json.loads(json.dumps(engine_result))
            with self.subTest(tool=tool), \
                    patch("knowledge_service.existing_service", return_value={}), \
                    patch("knowledge_service.ensure_service", return_value={}), \
                    patch("mindie_knowledge.loop.cli.rpc", return_value=served) as rpc:
                delivered = mcp_server.knowledge_call(tool, dict(call_args), SESSION)
            self.assertEqual(rpc.call_args.args[1], tool.removeprefix("knowledge_"))
            forwarded = rpc.call_args.args[2]
            self.assertEqual(forwarded["mode"], "explore")
            self.assertTrue(forwarded["native_subcontext"])
            self.assertEqual(delivered, engine_result)
            self.assertEqual(delivered["stripped_l3_ids"], ["unit-l3-workflow"])
            self.assertNotIn("unit-l3-workflow",
                             [row["id"] for row in (delivered.get("results", []) + delivered.get("units", []) + delivered.get("requires", []))])
            context = delivered["exploration_context"]
            pack = context["subagent_fact_pack"]
            self.assertEqual(pack["schema_version"], "mindie-rsi-subagent-pack/1")
            self.assertEqual(pack["isolation_mode"], "native_subcontext")
            # conditional_avoid (with its `avoid` field) and the usage estimate
            # are delivered exactly as the engine produced them.
            self.assertIn("avoid", context["conditional_avoid"][0])
            self.assertEqual(context["conditional_avoid"],
                             engine_result["exploration_context"]["conditional_avoid"])
            self.assertEqual(delivered["usage"], engine_result["usage"])


if __name__ == "__main__":
    unittest.main()
