import base64
from contextlib import closing
from email.parser import BytesParser
from email.policy import default
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import sqlite3
import tempfile
import unittest
import zipfile
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
        if self.path == '/api/skills/catalog': return self.respond(200, {'skills': [{'name': 'test-skill', 'source': 'local', 'enabled': True}], 'degraded': self.server.degraded})
        if self.path == '/api/org-policy': return self.respond(200, {'caps': {'operon_custom_skills': {'allowed': self.server.allowed}}})
        if self.path == '/api/skills/drafts': return self.respond(200, {'drafts': [p.name for p in self.server.skills_root.iterdir() if p.is_dir() and (p/'SKILL.md').is_file()]})
        if self.path == '/api/me': return self.respond(200, {'user_id': 'fixture-user'})
        if self.path == '/api/agents': return self.respond(200, [{'name': 'OPERON', 'skillNames': self.server.attached}])
        if self.path == '/api/skills/catalog/test-skill/content?path=SKILL.md': return self.respond(200, {'content': '# Skill\n<untrusted>'})
        if self.path.startswith('/api/skills/catalog/') and self.path.endswith('/content?path=SKILL.md'):
            name = self.path.split('/')[4]
            path = self.server.skills_root/name/'SKILL.md'
            if path.is_file(): return self.respond(200, {'content': 'corrupt' if self.server.corrupt_content else path.read_text()})
        self.respond(404, {'error': 'private-error-must-not-leak'})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if self.path == '/api/auth/nonce':
            assert json.loads(body)['nonce'] == 'fixture-nonce'
            return self.respond(200, {}, cookie='operon_auth=test-auth; Path=/')
        self.authenticated()
        if self.path == '/api/agents/OPERON/skills':
            if self.server.attach_fails: return self.respond(403, {})
            self.server.attached.append(json.loads(body)['skill_name'])
            return self.respond(200, {'name': 'OPERON', 'skillNames': self.server.attached})
        if self.path == '/api/skills/import':
            parsed = BytesParser(policy=default).parsebytes(('Content-Type: ' + self.headers['Content-Type'] + '\r\nMIME-Version: 1.0\r\n\r\n').encode() + body)
            parts = list(parsed.iter_parts())
            self.server.imported = (parts[0].get_filename(), parts[0].get_payload(decode=True))
            return self.respond(201, {'name': 'test-skill'})
        self.respond(404)

    def do_PUT(self):
        self.authenticated()
        self.server.enabled = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path.startswith('/api/skills/catalog/'):
            with closing(sqlite3.connect(self.server.dbpath)) as db, db:
                db.execute('INSERT OR REPLACE INTO capability_settings VALUES (?,?,?,?)', ('fixture-user','skill',self.path.split('/')[4],int(self.server.enabled['enabled'])))
        self.respond(200, {})

    def do_DELETE(self):
        self.authenticated()
        if self.path.startswith('/api/agents/OPERON/skills/'):
            name = self.path.split('/')[-1]
            self.server.attached = [s for s in self.server.attached if s != name]
            return self.respond(200, {})
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

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data, self.auth = self.root/'data', self.root/'auth'
        self.auth.mkdir()
        (self.auth/'active-org.json').write_text(json.dumps({'org_uuid':'12345678-1234-1234-1234-123456789abc'}))
        self.server.skills_root = self.data/'orgs/12345678-1234-1234-1234-123456789abc/skills'
        self.server.skills_root.mkdir(parents=True)
        self.server.dbpath = self.server.skills_root.parent/'operon-cli.db'
        with closing(sqlite3.connect(self.server.dbpath)) as db, db:
            db.execute('CREATE TABLE capability_settings(user_id TEXT,kind TEXT,key TEXT,enabled INTEGER,PRIMARY KEY(user_id,kind,key))')
        self.server.allowed = True
        self.server.degraded = False
        self.server.attach_fails = False
        self.server.attached = []
        self.server.corrupt_content = False

    def tearDown(self): self.temp.cleanup()

    def local_client(self): return bridge.Bridge(self.server.base, 'fixture-nonce', str(self.data), str(self.auth))

    def local_params(self, name='local-fixture'):
        text = f'---\nname: {name}\ndescription: Local fixture\n---\n# Read locally\n'
        return {'filename':'local.md','bundle':base64.b64encode(text.encode()).decode()}

    def test_degraded_catalog_imports_real_files_verifies_draft_and_attaches(self):
        self.server.degraded = True
        client = self.local_client()
        self.assertTrue(client.run('skills_list', {})['localImportAvailable'])
        result = client.run('skills_import', self.local_params())
        self.assertEqual(result['name'], 'local-fixture')
        self.assertTrue((self.server.skills_root/'local-fixture/SKILL.md').is_file())
        self.assertEqual(self.server.attached, ['local-fixture'])
        item = next(s for s in client.run('skills_list', {})['skills'] if s['name']=='local-fixture')
        self.assertEqual(item['source'], 'csswitch-local')
        self.assertEqual(item['attachedAgents'], ['OPERON'])
        client.run('skills_enabled', {'name':'local-fixture','enabled':False})
        item = next(s for s in client.run('skills_list', {})['skills'] if s['name']=='local-fixture')
        self.assertFalse(item['enabled'])
        client.run('skills_remove', {'name':'local-fixture'})
        self.assertFalse((self.server.skills_root/'local-fixture').exists())
        self.assertEqual(self.server.attached, [])

    def test_large_repository_skill_survives_library_bridge_install_and_toggle(self):
        # Reproduce ppt-master's >4 MiB, >13,000-file library through the real
        # subprocess protocols, including the old 6 MiB JSON input boundary.
        spec = importlib.util.spec_from_file_location('large_skill_workspace', Path(__file__).parents[1] / 'science-workspace.py')
        workspace = importlib.util.module_from_spec(spec); spec.loader.exec_module(workspace)
        home = self.root / 'home'; home.mkdir()
        config = home / '.csswitch-next'
        work = workspace.Workspace(home, self.data, config)
        archive = work.repo_path('fixture/ppt@main')
        archive.parent.mkdir(parents=True)
        payload = os.urandom(5 * 1024 * 1024)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('repo/skills/ppt-master/SKILL.md', '---\nname: ppt-master\ndescription: Large template library\n---\n')
            z.writestr('repo/skills/ppt-master/templates/sound.wav', payload)
            for i in range(13000): z.writestr(f'repo/skills/ppt-master/templates/icons/{i}.svg', '<svg/>')
            for path in ['.env.example', '.git/config', 'node_modules/unused.js', '__pycache__/unused.pyc']:
                z.writestr('repo/skills/ppt-master/' + path, 'not imported')
        workspace.write_json(work.pref, {'repos': [{'key': 'fixture/ppt@main', 'repo': 'fixture/ppt', 'branch': 'main'}]})
        key = work.discover()['skills'][0]['key']
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        def workspace_call(operation, params):
            request = {'home': str(home), 'data': str(self.data), 'config': str(config), 'operation': operation, 'params': params}
            result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'science-workspace.py')],
                                    input=json.dumps(request), capture_output=True, encoding='utf-8', env=env, timeout=120)
            self.assertEqual(result.returncode, 0, result.stderr)
            reply = json.loads(result.stdout)
            self.assertTrue(reply['ok'], reply.get('message'))
            return reply['value']
        workspace_call('skill_library_import', {'key': key})
        package = workspace_call('skill_bundle', {'key': 'library:ppt-master'})
        self.assertGreater(len(package['bundle']), 6 * 1024 * 1024)
        # File import uses the same larger stdin budget and archive rules.
        plan = workspace_call('skill_import_plan', package)
        self.assertEqual([s['name'] for s in plan['skills']], ['ppt-master'])
        self.server.degraded = True
        request = {'base': self.server.base, 'nonce': 'fixture-nonce', 'data_dir': str(self.data),
                   'auth_dir': str(self.auth), 'operation': 'skills_import', 'params': package}
        result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / 'science-mcp-manage.py')],
                                input=json.dumps(request), capture_output=True, encoding='utf-8', env=env, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)
        reply = json.loads(result.stdout)
        self.assertTrue(reply['ok'], reply.get('message'))
        installed = self.server.skills_root / 'ppt-master'
        self.assertEqual(hashlib.sha256((installed / 'templates/sound.wav').read_bytes()).digest(), hashlib.sha256(payload).digest())
        self.assertEqual(len(list((installed / 'templates/icons').glob('*.svg'))), 13000)
        self.assertFalse((installed / '.env.example').exists())
        self.assertEqual(self.server.attached, ['ppt-master'])
        client = self.local_client()
        for enabled in (False, True):
            client.run('skills_enabled', {'name': 'ppt-master', 'enabled': enabled})
            item = next(s for s in client.run('skills_list', {})['skills'] if s['name'] == 'ppt-master')
            self.assertEqual(item['enabled'], enabled)
            self.assertEqual(item['source'], 'csswitch-local')
        client.run('skills_remove', {'name': 'ppt-master'})
        self.assertFalse(installed.exists())

    def test_local_import_respects_policy_and_missing_native_paths(self):
        self.server.degraded = True
        self.server.allowed = False
        with self.assertRaisesRegex(bridge.BridgeError, '组织策略'):
            self.local_client().run('skills_import', self.local_params())
        self.server.allowed = True
        with self.assertRaisesRegex(bridge.BridgeError, '安装新版'):
            self.client().run('skills_import', self.local_params())
        self.assertFalse((self.server.skills_root/'local-fixture').exists())

    def test_local_update_preserves_disabled_state_binding_and_rolls_back_api_failure(self):
        self.server.degraded = True
        client = self.local_client()
        params = {**self.local_params(), 'sourceKey':'codex:local-fixture'}
        client.run('skills_import', params)
        client.run('skills_enabled', {'name':'local-fixture','enabled':False})
        item = next(s for s in client.run('skills_list', {})['skills'] if s['name']=='local-fixture')
        content = base64.b64decode(params['bundle']) + b'\nUpdated content'
        update = {**params, 'name':'local-fixture', 'bundle':base64.b64encode(content).decode(), 'expectedFingerprint':item['fingerprint']}
        self.server.corrupt_content = True
        with self.assertRaises(bridge.BridgeError): client.run('skills_update', update)
        self.assertEqual((self.server.skills_root/'local-fixture/SKILL.md').read_bytes(), base64.b64decode(params['bundle']))
        self.server.corrupt_content = False
        client.run('skills_update', update)
        self.assertEqual((self.server.skills_root/'local-fixture/SKILL.md').read_bytes(), content)
        self.assertEqual(self.server.attached, ['local-fixture'])
        current = next(s for s in client.run('skills_list', {})['skills'] if s['name']=='local-fixture')
        self.assertFalse(current['enabled'])
        self.assertEqual(current['sourceKey'], 'codex:local-fixture')
        self.server.allowed = False
        with self.assertRaises(bridge.BridgeError): client.run('skills_update',update)

    def test_failed_native_attachment_rolls_back_local_files(self):
        self.server.degraded = True
        self.server.attach_fails = True
        with self.assertRaisesRegex(bridge.BridgeError, '403'):
            self.local_client().run('skills_import', self.local_params())
        self.assertFalse((self.server.skills_root/'local-fixture').exists())

    def test_local_import_never_overwrites_existing_or_catalog_skills(self):
        self.server.degraded = True
        with self.assertRaisesRegex(bridge.BridgeError, '冲突'):
            self.local_client().run('skills_import', self.local_params('test-skill'))
        client = self.local_client()
        client.run('skills_import', self.local_params())
        original = (self.server.skills_root/'local-fixture/SKILL.md').read_bytes()
        with self.assertRaises(bridge.BridgeError): client.run('skills_import', self.local_params())
        self.assertEqual((self.server.skills_root/'local-fixture/SKILL.md').read_bytes(), original)

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
