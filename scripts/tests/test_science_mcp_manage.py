import importlib.util
import json
from pathlib import Path
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'science-mcp-manage.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, value=None, cookie=None):
        self.send_response(status)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if value is not None:
            self.wfile.write(json.dumps(value).encode())

    def do_GET(self):
        if self.path == '/api/csrf':
            return self.respond(204, cookie='operon_csrf=csrf-test; Path=/')
        assert self.headers.get('x-operon-csrf') == 'csrf-test'
        assert 'operon_auth=auth-test' in self.headers.get('Cookie', '')
        if self.path == '/api/mcp-servers/connectors':
            return self.respond(200, [{'id': 'local:sample', 'name': 'sample', 'source': 'local-stdio', 'transport': 'stdio', 'env': {'KEY': 'do-not-echo'}, 'headers': {'Authorization': 'do-not-echo'}, 'attachedAgents': []}])
        if self.path == '/api/mcp-servers/directory-health':
            return self.respond(200, {'directoryHealth': {'ok': False, 'status': 401, 'message': 'do-not-echo'}})
        if self.path == '/api/org-policy':
            return self.respond(200, {'caps': {'operon_custom_connectors': {'allowed': True}}})
        return self.respond(404)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        if self.path == '/api/auth/nonce':
            assert data['nonce'] == 'private-nonce'
            self.send_response(200)
            self.send_header('Set-Cookie', 'operon_auth=auth-test; Path=/; HttpOnly')
            self.end_headers()
            self.wfile.write(b'OK')  # Installed Science returns a non-JSON body here.
            return
        assert self.headers['Origin'] == self.server.base
        assert self.headers['x-operon-csrf'] == 'csrf-test'
        self.server.events.append((self.path, data))
        if self.path == '/api/mcp-servers/local':
            if data['name'] == 'fail':
                return self.respond(400, {'error': 'do-not-echo'})
            return self.respond(200, {'id': 'local:new', 'name': data['name'], 'env': data['env']})
        return self.respond(200, {'ok': True})


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.server.events = []
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def client(self):
        return bridge.Bridge(self.server.base, 'private-nonce')

    def test_cookie_and_csrf_flow_filters_private_configuration(self):
        data = self.client().run('list', {})
        self.assertTrue(data['customAllowed'])
        self.assertFalse(data['directoryHealth']['ok'])
        self.assertNotIn('do-not-echo', json.dumps(data))
        self.assertNotIn('private-nonce', json.dumps(data))

    def test_add_binds_operon_without_echoing_environment(self):
        result = self.client().run('add_local', {'name': 'new', 'command': '/usr/bin/python3', 'args': ['/tmp/test.py'], 'env': {'KEY': 'do-not-echo'}})
        self.assertTrue(result['attached'])
        self.assertNotIn('do-not-echo', json.dumps(result))
        self.assertIn(('/api/agents/OPERON/connectors', {'server_id': 'local:new'}), self.server.events)

    def test_external_loopback_endpoint_and_existing_name_are_rejected(self):
        for base in ['https://example.com', 'http://localhost:80', 'http://127.0.0.1:80/extra', 'http://u:p@127.0.0.1:80']:
            with self.assertRaises(bridge.BridgeError):
                bridge.Bridge(base, 'private-nonce')
        client = self.client()
        for params in [{'name': 'sample', 'command': '/usr/bin/python3'}, {'name': 'x', 'url': 'http://127.0.0.1:8080', 'transport': 'sse'}]:
            with self.assertRaises(bridge.BridgeError):
                client.run('add_local' if 'command' in params else 'add_remote', params)

    def test_http_error_never_returns_provider_error_body(self):
        with self.assertRaises(bridge.BridgeError) as error:
            self.client().run('add_local', {'name': 'fail', 'command': '/usr/bin/python3'})
        self.assertIn('HTTP 400', str(error.exception))
        self.assertNotIn('do-not-echo', str(error.exception))

    def test_local_connector_cannot_use_bundled_enable_endpoint(self):
        with self.assertRaises(bridge.BridgeError):
            self.client().run('enabled', {'id': 'local:sample', 'enabled': True})


if __name__ == '__main__':
    unittest.main()
