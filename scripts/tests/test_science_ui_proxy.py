import base64
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('ui_proxy', Path(__file__).parents[1] / 'science-ui-proxy.py')
ui = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ui)


class Native(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args): pass
    def do_GET(self):
        if self.path.startswith('/api/artifacts/') or self.path == '/api/me':
            self.server.artifact_requests = getattr(self.server, 'artifact_requests', []) + [(self.path, self.headers.get('Cookie'), self.headers.get('Authorization'))]
            authorized = self.headers.get('Cookie') == 'operon_auth=fixture' or self.headers.get('Authorization') == 'Bearer fixture'
            body = json.dumps(getattr(self.server, 'artifact', {})).encode()
            self.send_response(200 if authorized else 401)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Type', 'application/json')
            self.end_headers(); self.wfile.write(body)
            return
        if self.path == '/ws':
            self.send_response(101)
            self.send_header('Connection', 'Upgrade')
            self.send_header('Upgrade', 'websocket')
            self.end_headers()
            data = self.rfile.read(4)
            self.wfile.write(data)
            self.wfile.flush()
            self.close_connection = True
            return
        if self.path == '/api/models':
            body = json.dumps({'models': {'anthropic': [{'id': 'claude-opus-5', 'name': 'Claude Opus 5 → old · 质量', 'description':'CSSwitch 质量模型'}]}, 'default_model_id': 'claude-opus-5'}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
        elif self.path == '/redirect':
            body = b''
            self.send_response(302)
            self.send_header('Location', self.server.base + '/setup?nonce=fixture')
        elif self.path == '/events':
            body = b'data: first\n\ndata: second\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
        elif self.path == '/asset.js':
            body = b'// native asset'
            self.send_response(200)
            self.send_header('Content-Type', 'text/javascript')
        elif self.path == '/assets/native.js':
            body = b'const item={children:"Show archived sessions",title:"Translation fixture"};'
            self.send_response(200)
            self.send_header('Content-Type', 'application/javascript')
        else:
            body = b'<html><body><button>Settings</button><script>window.native=true</script></body></html>'
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
            self.send_header('Set-Cookie', 'operon_auth=fixture; HttpOnly; Path=/; SameSite=Strict')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def do_POST(self):
        data = self.rfile.read(int(self.headers['Content-Length']))
        body = json.dumps({'origin': self.headers['Origin'], 'cookie': self.headers.get('Cookie'), 'body': data.decode()}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.native = ThreadingHTTPServer(('127.0.0.1', 0), Native)
        self.native.base = f'http://127.0.0.1:{self.native.server_port}'
        self.proxy = ui.LoopbackServer(('127.0.0.1', 0), ui.Handler)
        self.proxy.config = Path(self.temp.name) / 'config.json'
        self.proxy.config.write_text(json.dumps({'science_zh': True, 'secret': 'NEVER-RETURN', 'providers': {'key': 'NEVER-RETURN'}}))
        self.proxy.upstream_port = self.native.server_port
        self.base = f'http://127.0.0.1:{self.proxy.server_port}'
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (self.native, self.proxy)]
        for t in self.threads: t.start()
    def tearDown(self):
        for s in (self.proxy, self.native): s.shutdown(); s.server_close()
        for t in self.threads: t.join()
        self.temp.cleanup()
    def request(self, path, **kwargs):
        return self.opener.open(urllib.request.Request(self.base + path, **kwargs), timeout=3)
    def test_loopback_bind_does_not_depend_on_hostname_lookup(self):
        with patch('socket.getfqdn', side_effect=AssertionError('unexpected DNS lookup')):
            server = ui.LoopbackServer(('127.0.0.1', 0), ui.Handler)
        try:
            self.assertEqual(server.server_name, 'localhost')
            self.assertGreater(server.server_port, 0)
        finally:
            server.server_close()
    def test_readiness_routes_work_without_native_and_keep_host_origin_checks(self):
        self.proxy.upstream_port = 0
        with self.request('/__csswitch/health') as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(json.load(r), {})
        with self.request('/health', headers={'Host': '127.0.0.1'}) as r:
            self.assertEqual(r.status, 200)
            self.assertEqual(json.load(r), {})
        for path, headers in [
            ('/__csswitch/health', {'Host': 'foreign.example'}),
            ('/__csswitch/health', {'Origin': 'https://foreign.example'}),
            ('/health', {'Host': '127.0.0.1', 'Origin': 'https://foreign.example'}),
            ('/', {'Host': '127.0.0.1'}),
        ]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request(path, headers=headers)
            self.assertEqual(error.exception.code, 403)
    def test_html_csp_cookie_and_asset(self):
        with self.request('/') as r:
            self.assertIn(ui.BOOTSTRAP.encode(), r.read())
            self.assertEqual(r.headers['Content-Security-Policy'], "frame-ancestors 'none'")
            self.assertIn('HttpOnly', r.headers['Set-Cookie'])
        with self.request('/asset.js') as r: self.assertEqual(r.read(), b'// native asset')
        restrictive = ui.script_policy("default-src 'self'; script-src 'nonce-native'; connect-src 'self'")
        self.assertIn("'nonce-native'", restrictive)
        self.assertIn(ui.SCRIPT_HASH, restrictive)
        self.assertIn("connect-src 'self'", restrictive)
    def test_config_is_live_and_never_exposes_credentials(self):
        with self.request('/__csswitch/config') as r:
            data = json.load(r)
            self.assertTrue(data['enabled'])
            self.assertFalse(data['online'])
            self.assertIsNone(data['translationToken'])
            self.assertNotIn('NEVER-RETURN', json.dumps(data))
        self.proxy.config.write_text('{"science_zh":false}')
        with self.request('/__csswitch/config') as r: self.assertFalse(json.load(r)['enabled'])
    def test_model_menu_reads_current_config_and_official_mode_is_untouched(self):
        cfg = {'mode': 'proxy', 'provider': 'test', 'providers': {'test': {'base_url':'https://example.invalid',
            'protocol':'responses', 'key':'NEVER-RETURN', 'model':'model-default', 'quality_model':'model-quality'}}}
        self.proxy.config.write_text(json.dumps(cfg)); self.proxy.config.chmod(0o600)
        with self.request('/api/models') as r:
            self.assertEqual(r.headers['Cache-Control'], 'no-store')
            body = r.read().decode(); data = json.loads(body)
        self.assertIn('model-quality', body)
        self.assertIn('Claude Opus', body)
        self.assertNotIn('NEVER-RETURN', body)
        self.assertEqual(data['default_model_id'], 'claude-opus-5')
        cfg['providers']['test']['quality_model'] = 'changed-quality'
        self.proxy.config.write_text(json.dumps(cfg))
        with self.request('/api/models') as r: self.assertIn('changed-quality', r.read().decode())
        cfg['mode'] = 'official'; self.proxy.config.write_text(json.dumps(cfg))
        with self.request('/api/models') as r: self.assertIn('old · 质量', json.load(r)['models']['anthropic'][0]['name'])

    def test_online_translation_requires_page_auth_and_only_uses_native_asset_literals(self):
        self.proxy.config.write_text('{"science_zh":true,"science_zh_online":true}')
        with self.request('/__csswitch/config') as r:
            token = json.load(r)['translationToken']
        with self.request('/assets/native.js') as r:
            self.assertIn('no-store', r.headers['Cache-Control']); self.assertIn(b'Show archived sessions', r.read())
        headers = {'Origin':self.base, 'Content-Type':'application/json', 'X-CSSwitch-Translation':token, 'Cookie':'operon_auth=fixture'}
        payload = json.dumps({'texts':['Show archived sessions', 'Private research title']}).encode()
        with patch.object(ui.Translator, 'request', return_value={'Show archived sessions':'显示已归档的会话'}) as request:
            for key, value in [('Origin','https://foreign.test'),('X-CSSwitch-Translation','bad'),('Cookie','bad')]:
                with self.subTest(key=key), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/__csswitch/zh/translate', data=payload, headers={**headers, key:value})
                self.assertIn(error.exception.code, (401,403))
            request.assert_not_called()
            with self.request('/__csswitch/zh/translate', data=payload, headers=headers) as r:
                result = json.load(r)
            request.assert_called_once_with(['Show archived sessions'])
            self.assertEqual(result['skipped'], ['Private research title'])
            with self.request('/__csswitch/zh/cache') as r:
                self.assertEqual(json.load(r)['translations'], result['translations'])
            self.proxy.config.write_text('{"science_zh":true,"science_zh_online":false}')
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/__csswitch/zh/translate', data=payload, headers=headers)
            self.assertEqual(error.exception.code, 403)

    def test_origin_cookie_forwarding_and_foreign_origin_rejection(self):
        with self.request('/api/csrf-fixture', data=b'{"test":true}', headers={'Origin': self.base, 'Cookie': 'operon_auth=fixture'}) as r:
            value = json.load(r)
            self.assertEqual(value['origin'], self.native.base)
            self.assertEqual(value['cookie'], 'operon_auth=fixture')
            self.assertEqual(value['body'], '{"test":true}')
        for headers in ({'Origin':'https://foreign.example'}, {'Host':'foreign.example'}):
            with self.assertRaises(urllib.error.HTTPError) as error: self.request('/', headers=headers)
            self.assertEqual(error.exception.code, 403)
    def test_sse_and_websocket_bytes(self):
        with self.request('/events') as r: self.assertEqual(r.read(), b'data: first\n\ndata: second\n\n')
        with socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=3) as sock:
            sock.sendall(f'GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{self.proxy.server_port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nOrigin: {self.base}\r\n\r\n'.encode())
            headers = b''
            while b'\r\n\r\n' not in headers: headers += sock.recv(1)
            self.assertIn(b'101', headers)
            sock.sendall(b'ping')
            self.assertEqual(sock.recv(4), b'ping')
    def test_redirect_keeps_local_entry_origin(self):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args): return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with self.assertRaises(urllib.error.HTTPError) as error: opener.open(self.base+'/redirect')
        self.assertEqual(error.exception.headers['Location'], self.base+'/setup?nonce=fixture')

    def enable_files(self):
        self.proxy.config.write_text('{"science_local_files":true}')
        self.document = Path(self.temp.name) / 'document.md'; self.document.write_text('fixture')
        self.native.artifact = {'version_id': 'v1', 'file_path': str(self.document)}
        with self.request('/__csswitch/files/config') as response:
            config = json.load(response)
        self.assertTrue(config['enabled'])
        self.file_headers = {'Origin': self.base, 'Content-Type': 'application/json', 'X-CSSwitch-Files': config['token'], 'Cookie': 'operon_auth=fixture'}
        self.file_payload = json.dumps({'artifactId': 'art_1', 'versionId': 'v1', 'action': 'reveal'}).encode()

    def test_local_files_plugin_config_is_independent_and_script_is_injected(self):
        with self.request('/__csswitch/files/config') as r: self.assertEqual(json.load(r), {'enabled': False, 'token': None})
        self.enable_files()
        with self.request('/__csswitch/config') as r: self.assertFalse(json.load(r)['enabled'])
        with self.request('/__csswitch/files.js') as r: self.assertIn(b'/__csswitch/files/action', r.read())
        with self.request('/') as r: self.assertIn(b"['zh','files','status']", r.read())

    def test_local_file_action_requires_origin_token_json_enabled_and_native_auth(self):
        self.enable_files()
        import science_local_files as files
        with patch.object(files.subprocess, 'run') as run:
            for key, value, status in [('Origin', None, 403), ('Origin', 'https://foreign.test', 403),
                    ('X-CSSwitch-Files', 'wrong', 403), ('Content-Type', 'text/plain', 415), ('Cookie', 'bad', 401)]:
                headers = {k: v for k, v in {**self.file_headers, key: value}.items() if v is not None}
                with self.subTest(key=key, value=value), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request('/__csswitch/files/action', data=self.file_payload, headers=headers)
                self.assertEqual(error.exception.code, status)
            self.proxy.config.write_text('{"science_local_files":false}')
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/__csswitch/files/action', data=self.file_payload, headers=self.file_headers)
            self.assertEqual(error.exception.code, 403)
        run.assert_not_called()

    def test_authenticated_local_action_and_auth_forwarding_without_downloads(self):
        self.enable_files()
        import science_local_files as files
        with patch.object(files.sys, 'platform', 'darwin'), patch.object(files.subprocess, 'run') as run:
            for credentials in [{'Cookie': 'operon_auth=fixture'}, {'Authorization': 'Bearer fixture'}]:
                headers = {k: v for k, v in self.file_headers.items() if k != 'Cookie'} | credentials
                with self.request('/__csswitch/files/action', data=self.file_payload, headers=headers) as response:
                    self.assertIn('Finder', json.load(response)['message'])
            self.assertEqual(run.call_count, 2)
        self.assertEqual(self.native.artifact_requests, [('/api/artifacts/art_1/metadata', 'operon_auth=fixture', None),
            ('/api/artifacts/art_1/metadata', None, 'Bearer fixture')])

    def test_action_rejects_get_raw_path_and_overlapping_operations(self):
        self.enable_files()
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/__csswitch/files/action', headers=self.file_headers)
        self.assertEqual(error.exception.code, 405)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('/__csswitch/files/action', data=json.dumps({'path': str(self.document), 'action': 'open'}).encode(), headers=self.file_headers)
        self.assertEqual(error.exception.code, 400)
        self.proxy.file_lock.acquire()
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request('/__csswitch/files/action', data=self.file_payload, headers=self.file_headers)
            self.assertEqual(error.exception.code, 409)
        finally: self.proxy.file_lock.release()



    def test_retired_model_picker_cannot_read_or_write_overrides(self):
        before = self.proxy.config.read_bytes()
        for path in ['/__csswitch/models.js', '/__csswitch/models/config',
                     '/__csswitch/models/catalog', '/__csswitch/models/bind', '/__csswitch/models/remember']:
            for body in (None, b'{}'):
                with self.subTest(path=path, body=body), self.assertRaises(urllib.error.HTTPError) as error:
                    self.request(path, data=body, headers={'Origin': self.base, 'Content-Type': 'application/json'})
                self.assertEqual(error.exception.code, 410)
        self.assertEqual(self.proxy.config.read_bytes(), before)
        self.assertFalse((self.proxy.config.parent / 'conversation-models').exists())
        with self.request('/') as response:
            html = response.read()
        self.assertIn(b"['zh','files','status']", html)
        self.assertNotIn(b"'models'", html)

    def test_legacy_choices_have_native_labels_and_no_saved_provider_override(self):
        cfg = {'provider': 'fixture', 'providers': {'fixture': {'protocol': 'responses',
            'base_url': 'https://example.invalid', 'key': 'NEVER-RETURN', 'model': 'current-default'}}}
        self.proxy.config.write_text(json.dumps(cfg)); self.proxy.config.chmod(0o600)
        directory = self.proxy.config.parent / 'conversation-models'; directory.mkdir()
        alias = 'claude-opus-99-01-20123456'
        (directory / (alias + '.json')).write_text('{"model":"retired-target","provider":"retired-provider"}')
        with self.request('/api/models') as response:
            data = json.load(response)
        self.assertNotIn(alias, [m['id'] for m in data['models']['anthropic']])
        self.assertEqual(len(data['models']['anthropic']), len(ui.load_profile(str(self.proxy.config), ui.PROVIDERS)['prov']['native_models']))
        self.assertTrue(all(' → current-default' in m['name'] for m in data['models']['anthropic']))
        self.assertTrue(all('csswitch_binding' not in m for m in data['models']['anthropic']))
        with self.request('/__csswitch/model-compat.js') as response:
            script = response.read().decode()
        self.assertNotIn('__CSSWITCH_NATIVE_IDS__', script)
        self.assertNotIn('NEVER-RETURN', script)
        self.assertNotIn('retired-target', json.dumps(data))
        self.assertNotIn('NEVER-RETURN', json.dumps(data))

    def test_retry_status_is_only_enabled_in_third_party_mode(self):
        self.proxy.config.write_text('{"mode":"proxy"}')
        with self.request('/__csswitch/status/config') as response:
            self.assertEqual(json.load(response), {'enabled': True})
        self.proxy.config.write_text('{"mode":"official"}')
        with self.request('/__csswitch/status/config') as response:
            self.assertEqual(json.load(response), {'enabled': False})


if __name__ == '__main__': unittest.main()
