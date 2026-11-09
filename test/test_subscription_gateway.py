"""Real CSSwitch HTTP gateway with loopback upstream and synthetic credentials only."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from subscription_store import Store, SubscriptionError
import subscription_gateway as module

BODY = json.dumps({'model': 'claude-sonnet-4-5', 'stream': True, 'max_tokens': 128,
 'messages': [{'role': 'user', 'content': 'Continue our local calculation'},
              {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'same-tool-id', 'name': 'compute', 'input': {'n': 30}}]},
              {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'same-tool-id', 'content': 'value=435'}]}],
 'tools': [{'name': 'compute', 'input_schema': {'type': 'object'}}]}).encode()
FULL = b'event: message_start\ndata: {"type":"message_start"}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.server.calls.append((self.headers.get('Authorization'), self.rfile.read(int(self.headers.get('Content-Length', 0)))))
        self.server.behavior(self)

    def do_GET(self):
        self.server.calls.append((self.headers.get('Authorization'), b''))
        self.server.behavior(self)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.store = Store(self.root / 'state')
        self.ids = [self.store.add({'account_uuid': name, 'email': name + '@example.test',
            'access_token': 'synthetic-' + name, 'refresh_token': 'refresh-' + name,
            'expires_at': time.time() + 3600, 'subscription_type': 'pro'}) for name in ('A', 'B')]
        self.upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
        self.upstream.daemon_threads = True
        self.upstream.calls = []
        self.upstream.behavior = lambda h: self.respond(h)
        self.gateway = module.Gateway(('127.0.0.1', 0), self.store, 'http://127.0.0.1:' + str(self.upstream.server_port))
        for server in (self.upstream, self.gateway):
            threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.02}, daemon=True).start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for server in (self.gateway, self.upstream):
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def respond(self, handler, status=200, body=FULL, sse=True, length=None, retry='90'):
        handler.send_response(status)
        handler.send_header('Content-Type', 'text/event-stream' if sse else 'application/json')
        handler.send_header('Content-Length', str(len(body) if length is None else length))
        handler.send_header('Retry-After', retry)
        handler.end_headers()
        handler.wfile.write(body)
        handler.wfile.flush()
        handler.close_connection = True

    def request(self, path='/v1/messages', headers=None, body=BODY, method='POST', secret=True):
        conn = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=5)
        try:
            conn.request(method, ('/' + self.gateway.secret if secret else '') + path, body=body,
                         headers=headers or {'Content-Type': 'application/json', 'anthropic-version': '2023-06-01'})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_429_falls_back_with_exact_history_and_tool_results(self):
        self.upstream.behavior = lambda h: self.respond(h, 429, b'{}') if h.headers['Authorization'] == 'Bearer synthetic-A' else self.respond(h)
        status, body = self.request()
        self.assertEqual((status, body), (200, FULL))
        self.assertEqual(self.upstream.calls, [('Bearer synthetic-A', BODY), ('Bearer synthetic-B', BODY)])
        state = self.store.snapshot()
        self.assertGreater(state['accounts'][self.ids[0]]['cooldown_until'], time.time() + 85)
        self.assertEqual(state['selected'], self.ids[1])
        self.request()
        self.assertEqual(len(self.upstream.calls), 3)
        self.assertEqual(self.upstream.calls[-1][0], 'Bearer synthetic-B')

    def test_deleted_account_is_not_restored_by_a_late_refresh(self):
        ident = self.ids[0]
        with self.store.locked():
            state = self.store.read()
            state['accounts'][ident]['expires_at'] = 0
            self.store.write(state)
        def refresh(*args, **kwargs):
            with self.store.locked():
                state = self.store.read()
                del state['accounts'][ident]
                state.update(selected='', mode='manual', revision=state['revision'] + 1)
                self.store.write(state)
            return {'access_token': 'late-refresh', 'expires_in': 3600}
        with patch.object(module, 'request_json', side_effect=refresh):
            with self.assertRaisesRegex(SubscriptionError, '已移除'):
                self.gateway.token(ident)
        with self.assertRaisesRegex(SubscriptionError, '已删除'):
            self.gateway.token(ident)
        self.assertNotIn(ident, self.store.snapshot()['accounts'])
        self.assertEqual(self.gateway.candidates()[1], [])

    def test_hot_switch_preserves_inflight_request_and_local_computation(self):
        started, release = threading.Event(), threading.Event()
        def behavior(h):
            if h.headers['Authorization'] == 'Bearer synthetic-A':
                started.set()
                release.wait(4)
            self.respond(h)
        self.upstream.behavior = behavior
        output = self.root / 'result.json'
        compute = subprocess.Popen([sys.executable, '-I', '-c',
            'import json,time,sys; time.sleep(.3); open(sys.argv[1],"w").write(json.dumps(sum(range(100000))))', str(output)])
        self.addCleanup(lambda: compute.kill() if compute.poll() is None else None)
        pid = compute.pid
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.request)
            self.assertTrue(started.wait(2))
            self.store.route(self.ids[1])
            second = pool.submit(self.request)
            self.assertEqual(second.result()[0], 200)
            release.set()
            self.assertEqual(first.result(), (200, FULL))
        self.assertEqual(self.store.snapshot()['selected'], self.ids[1])
        self.assertEqual([c[0] for c in self.upstream.calls], ['Bearer synthetic-A', 'Bearer synthetic-B'])
        self.assertEqual(compute.wait(timeout=3), 0)
        self.assertEqual(compute.pid, pid)
        self.assertEqual(json.loads(output.read_text()), sum(range(100000)))

    def test_partial_sse_is_never_replayed(self):
        self.upstream.behavior = lambda h: self.respond(h, body=b'event: content_block_delta\ndata: {"type":"content_block_delta"}\n\n')
        status, body = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b'event: error', body)
        self.assertEqual(len(self.upstream.calls), 1)
        self.assertEqual(self.gateway.last_account, '')
        self.assertIsNone(self.gateway.last_inference)

    def test_inference_evidence_excludes_models_and_counts(self):
        self.store.route(self.ids[0])
        self.assertEqual(self.request()[0], 200)
        evidence = self.gateway.last_inference.copy()
        self.assertEqual(evidence['account_id'], self.ids[0])
        self.assertEqual(evidence['revision'], self.store.snapshot()['revision'])
        self.assertGreaterEqual(evidence['completed_at'], evidence['started_at'])
        self.store.route(self.ids[1])
        self.upstream.behavior = lambda h: self.respond(h, body=b'{}', sse=False)
        self.assertEqual(self.request('/v1/models', method='GET', body=None)[0], 200)
        self.assertEqual(self.request('/v1/messages/count_tokens')[0], 200)
        self.assertEqual(self.gateway.last_account, self.ids[1])
        self.assertEqual(self.gateway.last_inference, evidence)
        self.upstream.behavior = lambda h: self.respond(h)
        self.assertEqual(self.request()[0], 200)
        status, raw = self.request('/health', method='GET', body=None)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)['last_inference']['account_id'], self.ids[1])
        self.assertNotIn(b'synthetic-', raw)
        self.assertNotIn(b'Continue our local calculation', raw)

    def test_truncated_json_is_error_without_replay(self):
        self.upstream.behavior = lambda h: self.respond(h, body=b'{"id":', sse=False, length=100)
        status, _ = self.request()
        self.assertEqual(status, 502)
        self.assertEqual(len(self.upstream.calls), 1)

    def test_ambiguous_network_failure_is_not_replayed(self):
        self.upstream.behavior = lambda h: h.connection.shutdown(2)
        self.assertEqual(self.request()[0], 502)
        self.assertEqual(len(self.upstream.calls), 1)

    def test_manual_does_not_fallback(self):
        self.store.route(self.ids[0])
        self.upstream.behavior = lambda h: self.respond(h, 429, b'{}')
        self.assertEqual(self.request()[0], 429)
        self.assertEqual(len(self.upstream.calls), 1)

    def test_native_owned_tokens_are_never_used_by_gateway(self):
        with self.store.locked():
            state = self.store.read()
            state['accounts'][self.ids[0]]['native_owned'] = True
            state['accounts'][self.ids[0]].pop('access_token')
            self.store.write(state)
        self.assertEqual(self.gateway.candidates()[1], [self.ids[1]])
        with self.store.locked():
            state = self.store.read()
            state['native_mode'] = True
            self.store.write(state)
        self.assertEqual(self.request()[0], 429)
        self.assertEqual(self.upstream.calls, [])

    def test_all_rejected_and_bad_request_are_bounded(self):
        self.upstream.behavior = lambda h: self.respond(h, 503, b'{}')
        self.assertEqual(self.request()[0], 503)
        self.assertEqual(len(self.upstream.calls), 2)
        self.assertEqual(self.request()[0], 429)
        self.assertEqual(len(self.upstream.calls), 2)

    def test_invalid_request_never_tries_second_account(self):
        self.upstream.behavior = lambda h: self.respond(h, 400, b'{}')
        self.assertEqual(self.request()[0], 400)
        self.assertEqual(len(self.upstream.calls), 1)

    def test_loopback_secret_origin_and_route_allowlist(self):
        self.assertEqual(self.request(secret=False)[0], 403)
        self.assertEqual(self.request(headers={'Host': 'attacker.test'})[0], 403)
        self.assertEqual(self.request(headers={'Origin': 'https://attacker.test'})[0], 403)
        self.assertEqual(self.request(path='/v1/unknown')[0], 404)
        self.assertEqual(self.request(headers={'Transfer-Encoding': 'chunked'})[0], 400)
        self.assertEqual(self.upstream.calls, [])
        status, body = self.request(path='/health', method='GET', body=None)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['service'], 'csswitch-subscriptions')
        self.assertNotIn(b'synthetic-', body)

    def test_pkce_callback_one_time_state_and_public_profile(self):
        url = self.gateway.begin_login()
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(urlsplit(url).hostname, 'claude.ai')
        self.assertEqual(query['code_challenge_method'], ['S256'])
        state = query['state'][0]
        with patch.object(module, 'request_json', side_effect=[
            {'access_token': 'synthetic-C', 'refresh_token': 'refresh-C', 'expires_in': 3600, 'scope': module.SCOPES},
            {'account': {'uuid': 'C', 'email_address': 'c@example.test'}, 'organization': {'uuid': 'org', 'organization_type': 'claude_pro'}}]) as request:
            ident = self.gateway.finish_login(state, 'synthetic-code')
            self.assertIn('code_verifier', request.call_args_list[0].args[1])
            self.assertEqual(self.store.snapshot()['accounts'][ident]['subscription_type'], 'pro')
            with self.assertRaises(SubscriptionError):
                self.gateway.finish_login(state, 'synthetic-code')
            with self.assertRaises(SubscriptionError):
                self.gateway.finish_login('spoofed', 'synthetic-code')
            self.assertEqual(request.call_count, 2)

    def test_expired_oauth_state_does_not_exchange(self):
        state = parse_qs(urlsplit(self.gateway.begin_login()).query)['state'][0]
        self.gateway.pending[state]['expires'] = 0
        with patch.object(module, 'request_json') as request:
            with self.assertRaises(SubscriptionError):
                self.gateway.finish_login(state, 'code')
            request.assert_not_called()

    def callback(self, state, code='synthetic-code', **extra):
        query = urlencode({'state': state, 'code': code, **extra})
        return self.request('/oauth/code/callback?' + query, method='GET', body=None, secret=False)

    def test_full_http_oauth_pkce_exchange_profile_and_encrypted_save(self):
        # Exercise actual HTTP serialization and headers, not mocked request_json.
        state = parse_qs(urlsplit(self.gateway.begin_login()).query)['state'][0]
        pending = self.gateway.pending[state].copy()
        seen = []
        def oauth(h):
            seen.append((h.path, h.headers.get('User-Agent')))
            if h.headers.get('User-Agent') != module.USER_AGENT:
                return self.respond(h, 403, b'{"error_code":1010}', sse=False)
            if h.path == '/token':
                form = parse_qs(self.upstream.calls[-1][1].decode())
                self.assertEqual(form['state'], [state])
                self.assertEqual(form['code_verifier'], [pending['verifier']])
                self.assertEqual(form['redirect_uri'], [pending['redirect']])
                self.assertEqual(form['client_id'], [module.CLIENT_ID])
                self.assertEqual(h.headers['Content-Type'], 'application/x-www-form-urlencoded')
                payload = {'access_token': 'synthetic-C', 'refresh_token': 'refresh-C', 'expires_in': 3600, 'scope': module.SCOPES}
            else:
                self.assertEqual(h.headers['Authorization'], 'Bearer synthetic-C')
                # Profile may return identity at the root, as the native client supports.
                payload = {'uuid': 'C', 'email': 'c@example.test', 'organization': {'organization_type': 'claude_pro'}}
            self.respond(h, body=json.dumps(payload).encode(), sse=False)
        self.upstream.behavior = oauth
        origin = 'http://127.0.0.1:' + str(self.upstream.server_port)
        with patch.object(module, 'TOKEN_URL', origin + '/token'), patch.object(module, 'PROFILE_URL', origin + '/profile'):
            status, page = self.callback(state)
        self.assertEqual(status, 200)
        self.assertIn('账号已添加'.encode(), page)
        self.assertEqual(seen, [('/token', module.USER_AGENT), ('/profile', module.USER_AGENT)])
        self.assertEqual(self.store.public()['login']['status'], 'success')
        self.assertEqual(self.store.public()['accounts'][-1]['plan'], 'pro')
        for path in (self.store.root / 'accounts.enc', self.store.root / 'login-status.json'):
            self.assertNotIn(b'synthetic-C', path.read_bytes())
        self.assertNotIn(b'synthetic-C', page)

    def test_callback_reports_exchange_403_not_expiry_and_redacts_body(self):
        state = parse_qs(urlsplit(self.gateway.begin_login()).query)['state'][0]
        self.upstream.behavior = lambda h: self.respond(h, 403, b'{"error_code":1010,"detail":"sensitive-code-and-token"}', sse=False)
        with patch.object(module, 'TOKEN_URL', 'http://127.0.0.1:' + str(self.upstream.server_port) + '/token'):
            status, page = self.callback(state)
        diagnostic = self.store.public()['login']
        self.assertEqual(status, 400)
        self.assertEqual((diagnostic['stage'], diagnostic['kind']), ('exchange', 'client_rejected'))
        self.assertIn(b'HTTP 403', page)
        self.assertNotIn('授权已过期'.encode(), page)
        self.assertNotIn(b'sensitive-code-and-token', page)
        self.assertNotIn('sensitive-code-and-token', json.dumps(diagnostic))
        self.assertEqual(len(self.store.public()['accounts']), 2)

    def test_profile_http_failure_has_own_stage(self):
        state = parse_qs(urlsplit(self.gateway.begin_login()).query)['state'][0]
        def oauth(h):
            if h.path == '/token':
                self.respond(h, body=b'{"access_token":"synthetic-C","expires_in":3600}', sse=False)
            else:
                self.respond(h, 401, b'{}', sse=False)
        self.upstream.behavior = oauth
        origin = 'http://127.0.0.1:' + str(self.upstream.server_port)
        with patch.object(module, 'TOKEN_URL', origin + '/token'), patch.object(module, 'PROFILE_URL', origin + '/profile'):
            status, _ = self.callback(state)
        self.assertEqual(status, 400)
        self.assertEqual(self.store.public()['login']['stage'], 'profile')
        self.assertEqual(self.store.public()['login']['kind'], 'http_401')

    def test_complete_token_identity_survives_optional_profile_failure(self):
        state = parse_qs(urlsplit(self.gateway.begin_login()).query)['state'][0]
        with patch.object(module, 'request_json', side_effect=[
            {'access_token': 'synthetic-C', 'expires_in': 3600, 'account': {'uuid': 'C', 'email': 'c@example.test'}},
            TimeoutError('private URL should never be exposed')]):
            status, _ = self.callback(state)
        self.assertEqual(status, 200)
        self.assertEqual(len(self.store.public()['accounts']), 3)

    def test_cancelled_or_stale_callback_does_not_exchange(self):
        state = parse_qs(urlsplit(self.gateway.begin_login()).query)['state'][0]
        with patch.object(module, 'request_json') as request:
            self.assertEqual(self.callback(state, code='', error='access_denied')[0], 400)
            self.assertEqual(self.store.public()['login']['kind'], 'authorization_denied')
            self.assertEqual(self.callback(state)[0], 400)
            self.assertEqual(self.store.public()['login']['kind'], 'state_expired')
            request.assert_not_called()

    def test_refresh_serialized_and_rotated_without_native_token_files(self):
        a = self.ids[0]
        with self.store.locked():
            state = self.store.read()
            state['accounts'][a]['expires_at'] = 1
            self.store.write(state)
        def refresh(*_args, **_kwargs):
            time.sleep(0.05)
            return {'access_token': 'new-A', 'refresh_token': 'rotated-A', 'expires_in': 3600}
        with patch.object(module, 'request_json', side_effect=refresh) as request:
            with ThreadPoolExecutor(max_workers=6) as pool:
                self.assertEqual(list(pool.map(self.gateway.token, [a] * 6)), ['new-A'] * 6)
            self.assertEqual(request.call_count, 1)
        self.assertEqual(self.store.snapshot()['accounts'][a]['refresh_token'], 'rotated-A')

    def test_upstream_redirect_cannot_receive_credentials(self):
        def redirect(h):
            h.send_response(302)
            h.send_header('Location', 'http://127.0.0.1:' + str(self.upstream.server_port) + '/stolen')
            h.send_header('Content-Length', '0')
            h.end_headers()
        self.upstream.behavior = redirect
        self.assertEqual(self.request()[0], 502)
        self.assertEqual(len(self.upstream.calls), 1)


if __name__ == '__main__':
    unittest.main()
