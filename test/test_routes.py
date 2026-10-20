import concurrent.futures
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "proxy"))
from routing import endpoint_for, load_profile
from stream_bridge import Bridge
import csswitch_proxy as cs


class RouteContracts(unittest.TestCase):
    def test_endpoints_and_transport_validation(self):
        self.assertEqual(endpoint_for("https://example.test", "anthropic"), "https://example.test/v1/messages")
        self.assertEqual(endpoint_for("https://example.test/v1", "responses"), "https://example.test/v1/responses")
        self.assertEqual(endpoint_for("https://example.test/api/v1/chat/completions", "openai"), "https://example.test/api/v1/chat/completions")
        for url in ("http://example.test", "https://user:password@example.test", "https://example.test?key=secret"):
            with self.assertRaises(ValueError):
                endpoint_for(url, "openai")

    def test_images_and_responses_tools_roundtrip(self):
        cs.PROV = cs.PROVIDERS["qwen"]
        chat = cs.anthropic_to_openai({"messages": [{"role": "user", "content": [
            {"type": "text", "text": "describe"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "abcd"}}]}]})
        self.assertEqual(chat["messages"][0]["content"][1]["image_url"]["url"], "data:image/png;base64,abcd")
        from responses_adapter import from_chat, to_anthropic
        out = from_chat(chat)
        self.assertEqual(out["input"][0]["content"][1]["type"], "input_image")
        response = to_anthropic({"output": [{"type": "function_call", "call_id": "call1", "name": "search", "arguments": '{"q":"science"}'}]}, "model")
        self.assertEqual(response["stop_reason"], "tool_use")
        self.assertEqual(response["content"][0]["input"], {"q": "science"})

    def test_truncated_stream_and_bad_tools_are_errors(self):
        class Handler:
            def _sse(self, *args):
                pass
        bridge = Bridge(Handler(), "model")
        bridge.chat({"choices": [{"delta": {"content": "partial"}}]})
        with self.assertRaises(ValueError):
            bridge.finish()
        bridge = Bridge(Handler(), "model")
        bridge.chat({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call", "function": {"name": "f", "arguments": "{"}}]}, "finish_reason": "tool_calls"}]})
        with self.assertRaises(ValueError):
            bridge.finish()


class GatewayIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = pathlib.Path(cls.temp.name)
        cls.hits = []
        cls.entered, cls.release = threading.Event(), threading.Event()

        class Mock(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cls.hits.append((self.path, body, dict(self.headers)))
                if self.path.startswith("/hold/"):
                    cls.entered.set()
                    cls.release.wait(5)
                protocol = "responses" if self.path.endswith("/responses") else "anthropic" if self.path.endswith("/messages") else "openai"
                if body.get("stream"):
                    if protocol == "openai":
                        items = [
                            {"choices": [{"index": 0, "delta": {"content": "hello"}}]},
                            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "call1", "function": {"name": "search", "arguments": '{"q":'}}]}}]},
                            {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"science"}'}}]}, "finish_reason": "tool_calls"}]},
                            {"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 5}},
                        ]
                    elif protocol == "responses":
                        items = [
                            {"type": "response.output_text.delta", "output_index": 0, "delta": "hello"},
                            {"type": "response.output_item.added", "output_index": 1, "item": {"type": "function_call", "name": "search", "call_id": "call1", "arguments": ""}},
                            {"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"q":"science"}'},
                            {"type": "response.completed", "response": {"usage": {"input_tokens": 11, "output_tokens": 5}}},
                        ]
                    else:
                        items = [
                            {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
                            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hello"}},
                            {"type": "message_delta", "usage": {"output_tokens": 5}},
                            {"type": "message_stop"},
                        ]
                    payload = b"".join(("data: " + json.dumps(i) + "\n\n").encode() for i in items)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    if protocol == "responses":
                        obj = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}], "usage": {"input_tokens": 11, "output_tokens": 5}}
                    elif protocol == "openai":
                        obj = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 11, "completion_tokens": 5}}
                    else:
                        obj = {"type": "message", "content": [{"type": "text", "text": "OK"}], "usage": {"input_tokens": 11, "output_tokens": 5}}
                    payload = json.dumps(obj).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
        threading.Thread(target=cls.upstream.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.upstream.server_port}"
        cls.config = cls.root / "config.json"
        cls.usage = cls.root / "usage.jsonl"
        cls.write_config("chat")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        cls.log = open(cls.root / "proxy.log", "w")
        cls.process = subprocess.Popen([sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "proxy/csswitch_proxy.py"),
            "--config", str(cls.config), "--usage-file", str(cls.usage), "--port", str(cls.port)],
            env={**os.environ, "CSSWITCH_AUTH_TOKEN": "test-path-secret"}, stdout=cls.log, stderr=cls.log)
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/test-path-secret/health", timeout=.2).close()
                break
            except OSError:
                time.sleep(.05)
        else:
            raise RuntimeError("Gateway did not start")

    @classmethod
    def write_config(cls, active):
        cfg = {"provider": active, "providers": {id: {"name": id, "protocol": protocol, "base_url": cls.base + "/" + id,
            "model": "model-" + id, "quality_model": "quality-" + id, "fast_model": "fast-" + id,
            "key": "test-key-" + id, "max_tokens": 8192} for id, protocol in
            [("chat", "openai"), ("responses", "responses"), ("native", "anthropic"), ("hold", "openai")]}}
        tmp = cls.config.with_suffix(".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f)
        os.replace(tmp, cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.release.set()
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.temp.cleanup()

    def request(self, stream=False, model="claude-opus-4-8"):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/test-path-secret/v1/messages",
            data=json.dumps({"model": model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 32, "stream": stream}).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer inbound-oauth-must-not-leak", "x-api-key": "inbound-key"})
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.read().decode()

    def test_all_protocols_stream_tools_credentials_and_usage(self):
        for id in ("chat", "responses", "native"):
            self.write_config(id)
            text = self.request(stream=True)
            self.assertIn('"text": "hello"', text)
            if id != "native":
                self.assertIn("input_json_delta", text)
                self.assertIn("tool_use", text)
            self.assertIn("message_stop", text)
            _, body, headers = self.hits[-1]
            self.assertEqual(body["model"], "quality-" + id)
            self.assertNotIn("inbound-oauth", json.dumps(headers))
            self.assertNotIn("inbound-key", json.dumps(headers))
            supplied = headers.get("X-Api-Key") if id == "native" else headers.get("Authorization")
            self.assertIn("test-key-" + id, supplied)
        entries = [json.loads(s) for s in self.usage.read_text().splitlines()]
        self.assertTrue(all(e["ok"] for e in entries))
        self.assertTrue(all(e["input_tokens"] == 11 and e["output_tokens"] == 5 for e in entries))
        self.assertEqual(os.stat(self.usage).st_mode & 0o777, 0o600)
        self.assertNotIn("test-key", self.usage.read_text())

    def test_science_family_routes_change_without_restarting_gateway(self):
        for active in ('chat', 'responses', 'native'):
            self.write_config(active)
            for alias, expected in [('claude-opus-5', 'quality-'), ('claude-opus-6', 'quality-'), ('claude-sonnet-6', 'model-'), ('claude-haiku-5', 'fast-')]:
                self.request(model=alias)
                self.assertEqual(self.hits[-1][1]['model'], expected + active)
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/test-path-secret/v1/models") as response:
                catalog = json.load(response)['data']
            self.assertTrue(any('quality-' + active in row['display_name'] for row in catalog))
        self.assertIsNone(self.process.poll())

    def test_hot_switch_keeps_inflight_route_and_same_process(self):
        self.write_config("hold")
        self.entered.clear()
        self.release.clear()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pending = pool.submit(self.request)
            self.assertTrue(self.entered.wait(3))
            self.write_config("responses")
            self.assertEqual(json.loads(self.request())["content"][0]["text"], "OK")
            self.release.set()
            self.assertEqual(json.loads(pending.result())["content"][0]["text"], "OK")
        self.assertIsNone(self.process.poll())
        hold_hit = next(hit for hit in reversed(self.hits) if hit[0].startswith("/hold/"))
        self.assertEqual(hold_hit[1]["model"], "quality-hold")
        self.assertEqual(hold_hit[2]["Authorization"], "Bearer test-key-hold")

    def test_probe_isolated_from_active_selection(self):
        self.write_config("chat")
        before = self.config.read_bytes()
        out = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve().parents[1] / "proxy/csswitch_proxy.py"),
            "--probe", "--provider", "responses", "--config", str(self.config)], capture_output=True, timeout=30)
        self.assertTrue(json.loads(out.stdout)["ok"])
        self.assertEqual(self.config.read_bytes(), before)
        self.assertTrue(self.hits[-1][0].endswith("/responses"))
        self.assertNotIn(b"test-key", out.stdout)

    def test_unsafe_config_permissions_fail_closed(self):
        os.chmod(self.config, 0o644)
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request()
            self.assertEqual(error.exception.code, 503)
        finally:
            os.chmod(self.config, 0o600)

    def test_unsaved_probe_all_protocols_without_writing_config(self):
        self.write_config("chat")
        before = self.config.read_bytes()
        script = str(pathlib.Path(__file__).resolve().parents[1] / "proxy/csswitch_proxy.py")
        for protocol in ("openai", "responses", "anthropic"):
            cfg = {"provider": "draft", "providers": {"draft": {"protocol": protocol,
                "base_url": self.base + "/draft", "model": "unsaved-model", "key": "private-draft-key", "max_tokens": 8192}}}
            out = subprocess.run([sys.executable, "-B", script, "--probe-stdin"], input=json.dumps(cfg).encode(), capture_output=True, timeout=30)
            self.assertTrue(json.loads(out.stdout)["ok"])
            self.assertEqual(self.hits[-1][1]["model"], "unsaved-model")
            self.assertIn("Reply OK.", json.dumps(self.hits[-1][1]))
            self.assertNotIn(b"private-draft-key", out.stdout + out.stderr)
            self.assertEqual(self.config.read_bytes(), before)

    def test_unsaved_probe_rejects_malformed_and_oversized_input(self):
        script = str(pathlib.Path(__file__).resolve().parents[1] / "proxy/csswitch_proxy.py")
        for payload in (b"private-broken-key", b"x" * 131073):
            out = subprocess.run([sys.executable, "-B", script, "--probe-stdin"], input=payload, capture_output=True, timeout=5)
            self.assertFalse(json.loads(out.stdout)["ok"])
            self.assertNotIn(b"private-broken-key", out.stdout + out.stderr)

if __name__ == "__main__":
    unittest.main()
