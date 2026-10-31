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
from routing import endpoint_for, load_profile, profile_from_config, model_endpoint, select_model_route
from stream_bridge import Bridge
import csswitch_proxy as cs
from private_files import protect_windows


class RouteContracts(unittest.TestCase):
    def test_endpoints_and_transport_validation(self):
        self.assertEqual(endpoint_for("https://example.test", "anthropic"), "https://example.test/v1/messages")
        self.assertEqual(endpoint_for("https://example.test/v1", "responses"), "https://example.test/v1/responses")
        self.assertEqual(endpoint_for("https://example.test/api/v1/chat/completions", "openai"), "https://example.test/api/v1/chat/completions")
        for url in ("http://example.test", "https://user:password@example.test", "https://example.test?key=secret"):
            with self.assertRaises(ValueError):
                endpoint_for(url, "openai")

    def test_role_protocol_defaults_validation_and_explicit_endpoint(self):
        cfg = {'provider': 'p', 'providers': {'p': {'protocol': 'responses',
            'base_url': 'https://example.test/api/v1/responses', 'key': 'private-key',
            'model': 'gpt-model', 'fast_protocol': 'anthropic'}}}
        plan = profile_from_config(cfg, {})
        self.assertEqual(plan['prov']['model_routes']['haiku']['mode'], 'responses')
        cfg['providers']['p'].update(fast_model='obsolete-model', fast_protocol='obsolete-protocol',
                                     quality_model='claude-sonnet-5-5', quality_protocol='anthropic')
        plan = profile_from_config(cfg, {})
        self.assertEqual(plan['prov']['model_routes']['haiku'], plan['prov']['model_routes']['sonnet'])
        self.assertEqual(plan['prov']['family_map']['haiku'], 'gpt-model')
        selected = select_model_route(plan, 'claude-opus-6', 'claude-sonnet-5-5')
        self.assertEqual(selected['prov']['mode'], 'anthropic')
        self.assertEqual(plan['prov']['mode'], 'responses')
        cfg['providers']['p']['quality_protocol'] = 'invalid'
        with self.assertRaises(ValueError):
            profile_from_config(cfg, {})
        with self.assertRaises(ValueError):
            model_endpoint('https://example.test/v1/messages', 'responses', 'anthropic')

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
                if body.get('model', '').startswith('error-'):
                    status = int(body['model'].split('-')[1])
                    payload = json.dumps({'error': {'message': 'fixture rejection', 'code': 'system_memory_overloaded' if status == 503 else 'fixture_error'}}).encode()
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers(); self.wfile.write(payload)
                    return
                if body.get('model') in ('quota-lock', 'auth-rejected'):
                    locked = body['model'] == 'quota-lock'
                    supplied = self.headers.get('x-api-key') or self.headers.get('Authorization', '').removeprefix('Bearer ')
                    error = {'message': ('database is locked (5) (SQLITE_BUSY)' if locked else 'invalid API key')
                             + ' (request id: fixture-request-id) ' + supplied,
                             'type': 'new_api_error',
                             'code': 'pre_consume_token_quota_failed' if locked else 'invalid_api_key'}
                    payload = json.dumps({'error': error}).encode()
                    self.send_response(403)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if self.path.startswith("/hold/") and body.get("model") == "quality-hold":
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
    def write_config(cls, active, changes=None):
        cfg = {"provider": active, "providers": {id: {"name": id, "protocol": protocol, "base_url": cls.base + "/" + id,
            "model": "model-" + id, "quality_model": "quality-" + id, "fast_model": "fast-" + id,
            "key": "test-key-" + id, "max_tokens": 8192} for id, protocol in
            [("chat", "openai"), ("responses", "responses"), ("native", "anthropic"), ("hold", "openai")]}}
        cfg["providers"][active].update(changes or {})
        tmp = cls.config.with_suffix(".tmp")
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(cfg, f)
        os.replace(tmp, cls.config)
        if os.name == 'nt': protect_windows(cls.config)

    @classmethod
    def tearDownClass(cls):
        cls.release.set()
        cls.process.terminate()
        cls.process.wait(timeout=5)
        cls.log.close()
        cls.upstream.shutdown()
        cls.upstream.server_close()
        cls.temp.cleanup()

    def request(self, stream=False, model="claude-opus-4-8", messages=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/test-path-secret/v1/messages",
            data=json.dumps({"model": model, "messages": messages if messages is not None else [{"role": "user", "content": "hello"}], "max_tokens": 32, "stream": stream}).encode(),
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
        if os.name != 'nt': self.assertEqual(os.stat(self.usage).st_mode & 0o777, 0o600)
        self.assertNotIn("test-key", self.usage.read_text())

    def test_mixed_protocol_roles_stream_nonstream_and_credentials(self):
        self.write_config('chat', {'quality_protocol': 'responses', 'fast_protocol': 'anthropic',
                                   'fast_model': 'claude-sonnet-5-5'})
        for stream in (False, True):
            for alias, suffix, model, protocol in [
                ('claude-sonnet-5', '/chat/completions', 'model-chat', 'openai'),
                ('claude-opus-6', '/responses', 'quality-chat', 'responses'),
                ('claude-haiku-5', '/chat/completions', 'model-chat', 'openai'),
                ('quality-chat', '/responses', 'quality-chat', 'responses'),
            ]:
                result = self.request(stream=stream, model=alias)
                self.assertIn('message_stop' if stream else 'OK', result)
                path, body, headers = self.hits[-1]
                self.assertEqual(path, '/chat/v1' + suffix)
                self.assertEqual(body['model'], model)
                self.assertEqual(headers.get('X-Api-Key') if protocol == 'anthropic' else headers.get('Authorization'),
                                 'test-key-chat' if protocol == 'anthropic' else 'Bearer test-key-chat')
                self.assertNotIn('inbound-', json.dumps(headers))
                self.assertEqual('input' in body, protocol == 'responses')
                if stream and protocol != 'anthropic':
                    self.assertIn('input_json_delta', result)
        for _ in range(30):
            entries = [json.loads(line) for line in self.usage.read_text().splitlines()]
            if entries[-1].get('model') == 'quality-chat':
                break
            time.sleep(.01)
        self.assertEqual(entries[-1]['protocol'], 'responses')

    def test_concurrent_mixed_requests_and_hot_edits_keep_inflight_protocol(self):
        self.write_config('hold', {'quality_protocol': 'responses', 'fast_protocol': 'anthropic'})
        self.entered.clear()
        self.release.clear()
        try:
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pending = pool.submit(self.request, True, 'claude-opus-5')
                self.assertTrue(self.entered.wait(3))
                self.assertIn('message_stop', self.request(stream=True, model='claude-haiku-4-5'))
                self.assertEqual(self.hits[-1][0], '/hold/v1/chat/completions')
                self.assertFalse(pending.done())
                self.write_config('hold', {'quality_protocol': 'anthropic', 'fast_protocol': 'openai'})
                self.assertIn('OK', self.request(model='claude-haiku-4-5'))
                self.assertEqual(self.hits[-1][0], '/hold/v1/chat/completions')
                self.release.set()
                self.assertIn('input_json_delta', pending.result())
        finally:
            self.release.set()
        hit = next(h for h in reversed(self.hits) if h[1]['model'] == 'quality-hold')
        self.assertEqual(hit[0], '/hold/v1/responses')
        self.assertEqual(hit[2]['Authorization'], 'Bearer test-key-hold')

    def test_probe_mixed_roles_without_saving_or_switching(self):
        self.write_config('chat')
        before = self.config.read_bytes()
        cfg = {'provider': 'draft', 'providers': {'draft': {
            'protocol': 'responses', 'base_url': self.base + '/draft/v1/responses',
            'model': 'gpt-default', 'quality_model': 'gpt-quality', 'fast_model': 'claude-fast',
            'fast_protocol': 'anthropic', 'key': 'probe-private-key'}}}
        script = str(pathlib.Path(__file__).resolve().parents[1] / 'proxy/csswitch_proxy.py')
        for role, model, suffix in [('default', 'gpt-default', '/responses'),
                                    ('quality', 'gpt-quality', '/responses'), ('fast', 'gpt-default', '/responses')]:
            out = subprocess.run([sys.executable, script, '--probe-stdin', '--probe-role', role],
                                 input=json.dumps(cfg).encode(), capture_output=True, timeout=30)
            self.assertTrue(json.loads(out.stdout)['ok'], out.stdout)
            self.assertEqual(self.hits[-1][0], '/draft/v1' + suffix)
            self.assertEqual(self.hits[-1][1]['model'], model)
            self.assertNotIn(b'probe-private-key', out.stdout + out.stderr)
        self.assertEqual(self.config.read_bytes(), before)

    def test_science_family_routes_change_without_restarting_gateway(self):
        for active in ('chat', 'responses', 'native'):
            self.write_config(active)
            for alias, expected in [('claude-opus-5', 'quality-'), ('claude-opus-6', 'quality-'), ('claude-sonnet-6', 'model-'), ('claude-haiku-5', 'model-')]:
                self.request(model=alias)
                self.assertEqual(self.hits[-1][1]['model'], expected + active)
            with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/test-path-secret/v1/models") as response:
                catalog = json.load(response)['data']
            self.assertTrue(any('Claude Opus' in row['display_name'] for row in catalog))
        self.assertIsNone(self.process.poll())

    def test_quota_database_lock_explains_provider_failure_and_preserves_status(self):
        for active in ('chat', 'responses', 'native'):
            for stream in (False, True):
                for model in ('quota-lock', 'auth-rejected'):
                    with self.subTest(protocol=active, stream=stream, model=model):
                        self.write_config(active, {'quality_model': model})
                        count = len(self.hits)
                        with self.assertRaises(urllib.error.HTTPError) as result:
                            self.request(stream=stream)
                        self.assertEqual(result.exception.code, 403)
                        message = json.load(result.exception)['error']['message']
                        self.assertIn('fixture-request-id', message)
                        self.assertIn('upstream 403:', message)
                        self.assertNotIn('test-key-', message)
                        self.assertEqual('额度预扣数据库暂时被锁定' in message, model == 'quota-lock')
                        self.assertIn('pre_consume_token_quota_failed' if model == 'quota-lock' else 'invalid_api_key', message)
                        self.assertEqual(len(self.hits), count + 1)

    def test_switching_protocol_normalizes_long_tool_history_ids_and_pairs(self):
        long_id = 'toolu_' + 'x' * 60
        messages = [{'role': 'assistant', 'content': [{'type':'tool_use','id':long_id,'name':'search','input':{}}]},
                    {'role':'user','content':[{'type':'tool_result','tool_use_id':long_id,'content':'OK'}]}]
        for active in ('chat', 'responses', 'native'):
            self.write_config(active)
            for stream in (False, True):
                self.request(stream=stream, messages=messages)
                body = self.hits[-1][1]
                if active == 'responses':
                    call, result = body['input']
                    ids = call['call_id'], result['call_id']
                elif active == 'chat':
                    call, result = body['messages']
                    ids = call['tool_calls'][0]['id'], result['tool_call_id']
                else:
                    call, result = body['messages']
                    ids = call['content'][0]['id'], result['content'][0]['tool_use_id']
                self.assertEqual(ids[0], ids[1])
                self.assertLessEqual(len(ids[0]), 64)
        self.assertEqual(messages[0]['content'][0]['id'], long_id)

    def test_upstream_parameter_body_size_and_overload_statuses_are_not_converted_to_502(self):
        for active in ('chat', 'responses', 'native'):
            for status, kind in ((400,'invalid_request_error'),(413,'request_too_large'),(503,'overloaded_error')):
                self.write_config(active, {'quality_model': 'error-' + str(status)})
                for stream in (False, True):
                    with self.assertRaises(urllib.error.HTTPError) as result:
                        self.request(stream=stream)
                    self.assertEqual(result.exception.code, status)
                    error = json.load(result.exception)['error']
                    self.assertEqual(error['type'], kind)
                    if status == 503:self.assertIn('API 服务商内存过载', error['message'])

    def test_legacy_conversation_aliases_follow_active_csswitch_mapping(self):
        self.write_config('chat', {'quality_protocol': 'responses', 'fast_protocol': 'anthropic'})
        before = self.config.read_bytes()
        aliases = ['claude-opus-99-01-20123456', 'claude-haiku-99-02-20654321']
        directory = self.config.parent / 'conversation-models'; directory.mkdir(exist_ok=True)
        record = directory / (aliases[0] + '.json')
        record.write_text(json.dumps({'provider': 'removed-provider', 'model': 'stale-override', 'protocol': 'anthropic'}))
        original = record.read_bytes()
        start = len(self.hits)
        with concurrent.futures.ThreadPoolExecutor() as pool:
            results = list(pool.map(lambda alias: self.request(model=alias), aliases * 3))
        self.assertTrue(all('OK' in result for result in results))
        self.assertEqual({(path, body['model']) for path, body, _ in self.hits[start:]},
            {('/chat/v1/responses', 'quality-chat'), ('/chat/v1/chat/completions', 'model-chat')})
        self.assertEqual(before, self.config.read_bytes())
        self.assertEqual(original, record.read_bytes())
        self.write_config('native', {'quality_model': 'new-quality'})
        self.request(model=aliases[0])
        self.assertEqual(self.hits[-1][0], '/native/v1/messages')
        self.assertEqual(self.hits[-1][1]['model'], 'new-quality')
        record.unlink()
        self.request(model=aliases[0])
        self.assertEqual(self.hits[-1][1]['model'], 'new-quality')

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

    @unittest.skipIf(os.name == 'nt', 'POSIX modes; Windows DACL has dedicated coverage')
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
