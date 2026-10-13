import base64
from email.parser import BytesParser
from email.policy import default
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

spec = importlib.util.spec_from_file_location('skills_bridge', Path(__file__).parents[1] / 'science-mcp-manage.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def respond(self, status, value=None, cookie=None):
        self.send_response(status)
        if cookie: self.send_header('Set-Cookie', cookie)
        self.end_headers()
        if value is not None: self.wfile.write(json.dumps(value).encode())

    def authenticated(self):
        assert self.headers.get('x-operon-csrf') == 'test-csrf'
        assert 'operon_auth=test-auth' in self.headers.get('Cookie', '')
        assert self.headers.get('Origin') == self.server.base

    def do_GET(self):
        if self.path == '/api/csrf': return self.respond(204, cookie='operon_csrf=test-csrf; Path=/')
        self.authenticated()
        if self.path == '/api/skills/catalog': return self.respond(200, {'skills': [{'name': 'test-skill', 'source': 'local', 'enabled': True}], 'degraded': False})
        if self.path == '/api/skills/catalog/test-skill/content?path=SKILL.md': return self.respond(200, {'content': '# Skill\n<untrusted>'})
        self.respond(404, {'error': 'private-error-must-not-leak'})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/api/auth/nonce':
            assert json.loads(body)['nonce'] == 'fixture-nonce'
            return self.respond(200, {}, cookie='operon_auth=test-auth; Path=/')
        self.authenticated()
        if self.path == '/api/skills/import':
            parsed = BytesParser(policy=default).parsebytes(('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
            parts = list(parsed.iter_parts())
            self.server.imported = (parts[0].get_filename(), parts[0].get_payload(decode=True))
            return self.respond(201, {'name': 'test-skill'})
        self.respond(404)

    def do_PUT(self):
        self.authenticated()
        self.server.enabled = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.respond(200, {})

    def do_DELETE(self):
        self.authenticated()
        if self.path == '/api/skills/test-skill/full': return self.respond(200, {})
        self.respond(403, {'error': 'private-error-must-not-leak'})


class SkillsBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True); cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def client(self): return bridge.Bridge(self.server.base, 'fixture-nonce')

    def test_catalog_and_content_use_authenticated_science_routes(self):
        self.assertEqual(self.client().run('skills_list', {})['skills'][0]['name'], 'test-skill')
        self.assertIn('<untrusted>', self.client().run('skills_content', {'name': 'test-skill'})['content'])

    def test_import_multipart_preserves_file_bytes_and_never_echoes_bundle(self):
        content = b'---\nname: test-skill\n---\n# Fixture\n'
        result = self.client().run('skills_import', {'filename': 'test-skill.md', 'bundle': base64.b64encode(content).decode()})
        self.assertEqual(self.server.imported, ('test-skill.md', content))
        self.assertNotIn('bundle', result)

    def test_enable_boolean_and_delete_are_policy_checked_by_science(self):
        self.client().run('skills_enabled', {'name': 'test-skill', 'enabled': False})
        self.assertEqual(self.server.enabled, {'enabled': False})
        self.client().run('skills_remove', {'name': 'test-skill'})
        with self.assertRaises(bridge.BridgeError) as error: self.client().run('skills_remove', {'name': 'protected'})
        self.assertNotIn('private-error', str(error.exception))
        self.assertIn('403', str(error.exception))

    def test_invalid_imports_and_paths_are_rejected(self):
        for p in [{'filename': '../escape.md', 'bundle': 'dGVzdA=='}, {'filename': 'test.md', 'bundle': 'invalid'}, {'filename': 'test.md', 'bundle': ''}, {'filename': 'bad.exe', 'bundle': 'dGVzdA=='}]:
            with self.subTest(p=p), self.assertRaises(bridge.BridgeError): self.client().run('skills_import', p)
        with self.assertRaises(bridge.BridgeError): self.client().run('skills_content', {'name': '../escape'})
        with self.assertRaises(bridge.BridgeError): self.client().run('skills_enabled', {'name': 'test', 'enabled': 'false'})


if __name__ == '__main__': unittest.main()
