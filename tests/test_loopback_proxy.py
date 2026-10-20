import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from support import SCRIPTS

sys.path.insert(0, str(SCRIPTS))
import install_kimi_plugin
import updater


class LoopbackProxyTests(unittest.TestCase):
    def test_native_registration_stays_local_with_proxy_and_no_bypass(self):
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append((
                    self.headers["Authorization"],
                    json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
                ))
                body = b'{"data":{"id":"mindie-agent","hasErrors":false}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
            )
            thread.start()
            env = {
                key: value for key, value in os.environ.items()
                if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
            }
            env.update(HTTP_PROXY="http://127.0.0.1:1", NO_PROXY="")
            try:
                with patch.dict(os.environ, env, clear=True):
                    result = install_kimi_plugin.call(
                        server.server_port, "local-token", "POST",
                        "/api/plugins/install", {"source": "local"},
                    )
                self.assertEqual(result["http"], 200)
                self.assertFalse(result["body"]["data"]["hasErrors"])
                self.assertEqual(calls, [("Bearer local-token", {"source": "local"})])
            finally:
                server.shutdown()
                thread.join(timeout=2)

    def test_scheduled_update_restores_network_and_respects_current_caller(self):
        from mindie_diagnostics.service_environment import prepare

        with tempfile.TemporaryDirectory() as raw, patch.dict(os.environ):
            root = Path(raw)
            config = root / "kimi.json"
            config.write_text(json.dumps({"state_dir": str(root)}))
            os.environ["HTTPS_PROXY"] = "http://saved-proxy.invalid:8080"
            os.environ["UV_DEFAULT_INDEX"] = "https://packages.example.invalid/simple"
            prepare(root / "update")
            del os.environ["HTTPS_PROXY"]
            del os.environ["UV_DEFAULT_INDEX"]
            observed = []

            def check():
                self.assertEqual(
                    os.environ["UV_DEFAULT_INDEX"], "https://packages.example.invalid/simple"
                )
                observed.append(os.environ["HTTPS_PROXY"])
                return 0

            with patch.object(updater, "check", check):
                self.assertEqual(updater.main(["check", "--config", str(config)]), 0)
                os.environ["HTTPS_PROXY"] = "http://current-proxy.invalid:8080"
                self.assertEqual(updater.main(["check", "--config", str(config)]), 0)
            self.assertEqual(observed, [
                "http://saved-proxy.invalid:8080", "http://current-proxy.invalid:8080"
            ])
