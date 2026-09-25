"""Schema and knowledge-page wire. No native host is attached.

A call without a locally published PreToolUse bind fails closed. The page
is a local Store.explain fixture from the installed mindie_knowledge
dependency; the helper forwards that object, including next_offset.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, env_for, make_config

PAGE = 32768
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
    if args.get("offset") != int(os.environ["OFFSET"]):
        raise AssertionError(args.get("offset"))
    if args.get("limit") != 32768:
        raise AssertionError(args.get("limit"))
    if "request_nonce" in args or "session_id" in args or "_session_id" in args:
        raise AssertionError("identity leaked into the knowledge call")
    return page

mcp_server.knowledge_call = knowledge_call
response = mcp_server.handle("knowledge", json.loads(sys.stdin.read()))
if response is None:
    raise SystemExit("helper returned no response")
sys.stdout.write(mcp_server.canonical(response) + "\n")
"""

STUB = r"""
import json, sys
message = json.loads(sys.stdin.read())
blob = "\u6d4b" * 88000
body = {
    "jsonrpc": "2.0",
    "id": message["id"],
    "result": {"content": [{"type": "text", "text": blob}], "isError": False},
}
sys.stdout.write(json.dumps(body, ensure_ascii=False))
"""


def _bodies():
    extra = 8
    escape = ('\\"' * ((PAGE + extra) // 2 + 1))[: PAGE + extra]
    return {
        "ascii": "A" * (PAGE + extra),
        "chinese": "\u6d4b" * (PAGE + extra),
        "nonbmp": "\U00020000" * (PAGE + extra),
        "escape": escape,
    }


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

    def test_explain_schema_maximum_and_query_unchanged(self):
        import mcp_server

        explain = next(item for item in mcp_server.KNOWLEDGE_TOOLS if item["name"] == "knowledge_explain")
        query = next(item for item in mcp_server.KNOWLEDGE_TOOLS if item["name"] == "knowledge_query")
        self.assertEqual(explain["inputSchema"]["properties"]["limit"]["maximum"], PAGE)
        self.assertEqual(explain["inputSchema"]["required"], ["ref", "request_nonce"])
        self.assertIn("next_offset", explain["description"])
        self.assertIn("slice", explain["description"])
        self.assertNotIn("offset", query["inputSchema"]["properties"])
        self.assertEqual(query["inputSchema"]["properties"]["limit"]["maximum"], 20)
        self.assertEqual(
            set(query["inputSchema"]["properties"]),
            {"query", "limit", "conditions", "request_nonce"},
        )

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

    def test_store_explain_page_fits_knowledge_bound(self):
        import bounded
        import mindie_launch
        from mindie_knowledge.loop.store import Store

        knowledge_bound = mindie_launch.KNOWLEDGE_MAX_OUTPUT

        helper = self.tmp / "helper.py"
        helper.write_text(HELPER)
        store = Store(self.tmp / "store", "vllm-ascend")
        sizes = {}
        nonbmp_out = None
        observed = {
            "explain_page_chars": getattr(Store, "EXPLAIN_PAGE_CHARS", None),
            "explain_max_limit": getattr(Store, "EXPLAIN_MAX_LIMIT", None),
        }
        try:
            for name, body in _bodies().items():
                self.assertEqual(len(body), PAGE + 8)
                doc = store.create_draft(
                    kind="knowledge",
                    title="Local page fixture",
                    summary="Local component fixture only.",
                    content=body,
                )
                ref = store.ref(doc["entry_id"], doc["revision"])
                offset = 0
                for _ in range(3):
                    page = store.explain(ref, offset=offset, limit=PAGE)
                    out = self._helper(bounded, helper, name, ref, offset, page)
                    response = json.loads(out)
                    result = response["result"]
                    self.assertIs(result["isError"], False)
                    text = json.loads(result["content"][0]["text"])
                    structured = result["structuredContent"]
                    self.assertEqual(text, page)
                    self.assertEqual(structured, page)
                    self.assertEqual(structured["ref"], ref)
                    self.assertEqual(structured["content_offset"], offset)
                    nbytes = len(out.encode())
                    self.assertLessEqual(nbytes, knowledge_bound, f"{name} {nbytes}")
                    self.assertIn("next_offset", structured)
                    self.assertEqual(structured["next_offset"], page["next_offset"])
                    if offset == 0:
                        self.assertEqual(len(structured["content"]), PAGE)
                        self.assertEqual(structured["content"], body[:PAGE])
                        sizes[name] = nbytes
                        if name == "nonbmp":
                            nonbmp_out = out
                    else:
                        self.assertEqual(structured["content"], body[offset:])
                    nxt = page["next_offset"]
                    if nxt is None:
                        self.assertEqual(offset + len(page["content"]), page["content_length"])
                        break
                    self.assertIsInstance(nxt, int)
                    self.assertEqual(nxt, offset + len(page["content"]))
                    offset = nxt
                else:
                    self.fail("paging did not reach the end")
        finally:
            store.close()
        self.assertGreater(sizes["nonbmp"], bounded.MAX_OUTPUT)
        with self.assertRaises(bounded.OutputLimitExceeded):
            bounded.run(
                [sys.executable, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
                nonbmp_out,
                timeout=10,
                max_output=bounded.MAX_OUTPUT,
            )
        observed["bytes"] = sizes
        observed["bound"] = knowledge_bound
        observed["previous_default"] = bounded.MAX_OUTPUT
        print("KNOWLEDGE_PAGE_BYTES " + json.dumps(observed, sort_keys=True))

    def _helper(self, bounded, helper, name, ref, offset, page):
        import identity
        import mindie_launch

        path = self.tmp / f"{name}-{offset}.json"
        path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
        nonce = f"nonce-{name}-{offset}"
        arguments = {
            "ref": ref,
            "offset": offset,
            "limit": PAGE,
            "request_nonce": nonce,
        }
        identity.publish_nonce_bind(
            SESSION, nonce, "knowledge_explain", f"tool-{name}-{offset}", arguments
        )
        message = {
            "jsonrpc": "2.0",
            "id": f"id-{name}-{offset}",
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
                "OFFSET": str(offset),
                "SESSION": SESSION,
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

    def test_dispatch_raises_knowledge_bound_only(self):
        import bounded
        import mindie_launch

        generation = self.tmp / "generation"
        scripts = generation / "scripts"
        scripts.mkdir(parents=True)
        (scripts / "mcp_server.py").write_text(STUB)
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
        response = mindie_launch._dispatch("knowledge", raw, "cap-1")
        text = response["result"]["content"][0]["text"]
        self.assertGreater(len(text.encode()), 256 * 1024)
        self.assertLess(len(text.encode()), mindie_launch.KNOWLEDGE_MAX_OUTPUT)
        self.assertEqual(bounded.MAX_OUTPUT, 256 * 1024)
        with self.assertRaises(mindie_launch.DispatchFailure) as caught:
            mindie_launch._dispatch("remote", raw, "cap-1")
        self.assertEqual(caught.exception.stage, "helper_failed")

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


if __name__ == "__main__":
    unittest.main()
