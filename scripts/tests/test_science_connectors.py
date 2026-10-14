import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


profiles = load('profiles', 'science-connectors.py')
bridge = load('bridge', 'science-mcp-manage.py')


class FakeBridge(bridge.Bridge):
    def __init__(self, home):
        self.home = home
        self.rows = [dict(id='bundled:' + k, name=k, source='bundled', enabled=True,
                          connectionStatus='connected', attachedAgents=['OPERON']) for k in profiles.NAMES]
        self.events = []
        self.fail = None
        self.pending = None

    def connectors(self):
        return self.rows

    def request(self, path, data=None, method=None, content_type=None):
        self.events.append((path, data, method))
        if path == '/api/org-policy':
            return {'caps': {'operon_custom_connectors': {'allowed': True}}}
        if path == '/api/mcp-servers/local':
            if data['name'] == self.fail:
                raise bridge.BridgeError('mock service failed')
            row = next((c for c in self.rows if c.get('name') == data['name']), None)
            if not row:
                row = dict(id='local:' + data['name'], source='local-stdio',
                           connectionStatus='connected', attachedAgents=[])
                self.rows.append(row)
            row.update(data)
            source = Path(self.home) / '.claude-science/mcp/local-mcp.json'
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps({'servers': [c for c in self.rows if c['source'] == 'local-stdio']}))
            return row
        if path == '/api/agents/OPERON/connectors':
            next(c for c in self.rows if c['id'] == data['server_id'])['attachedAgents'] = ['OPERON']
            return {}
        if 'tool-permissions?' in path:
            return {'status': 'pending' if self.pending and self.pending in path else 'loaded', 'tools': [{'toolName': 'real-tool'}]}
        if path.endswith('/enabled'):
            key = path.split('/connectors/')[1].rsplit('/enabled', 1)[0]
            next(c for c in self.rows if c['id'] == key.replace('%3A', ':'))['enabled'] = data['enabled']
        if method == 'DELETE' and '/local/' in path:
            key = path.rsplit('/', 1)[1].replace('%3A', ':')
            self.rows = [c for c in self.rows if c['id'] != key]
        return {}


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.root = self.home / '.claude-science/runtime/0.1.62-release'
        for name in ['.ok', 'mcp-servers/bio-tools/run_server.py', 'mcp-servers/ketcher-chemistry/server.js', 'mcp-servers/ketcher-chemistry/widget/index.html']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        for key in profiles.NAMES:
            if key == 'ketcher-chemistry':
                continue
            path = self.root / f'mcp-servers/bio-tools/lib/mcp_{key.replace("-", "_")}/server.py'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        executable = self.home / '.claude-science/conda/envs/claude-science-mcp/bin/python'
        executable.parent.mkdir(parents=True)
        executable.touch()
        self.params = dict(home=str(self.home), launcher='/app/scripts/science-connector-run.py', proxy_port=18992, node='/usr/bin/true')

    def test_all_profiles_use_local_resources_and_keep_original_names(self):
        self.assertEqual(len(profiles.NAMES), 24)
        for key in profiles.NAMES:
            spec = profiles.spec(self.home, key, self.params['launcher'], 18992, '/usr/bin/true')
            self.assertTrue(any(str(self.root) in argument for argument in spec['args']))
            self.assertEqual(spec['env']['HTTPS_PROXY'], 'http://127.0.0.1:18992')
            self.assertNotIn('claude.ai', json.dumps(spec))
        with self.assertRaises(ValueError):
            profiles.command(self.home, '../../other')
        newer = self.home / '.claude-science/runtime/0.1.70-release'
        newer.mkdir()
        self.assertEqual(profiles.runtime(self.home), self.root)  # incomplete update ignored

    def test_install_preserves_twenty_native_ids_and_is_idempotent(self):
        client = FakeBridge(str(self.home))
        result = client.install_science({**self.params, 'env': {'NCBI_EMAIL': 'test@example.invalid'}})
        self.assertTrue(result['complete'])
        self.assertEqual(sum(p['ready'] for p in result['scienceProfiles']), 24)
        count = len(client.rows)
        again = client.install_science(self.params)
        self.assertTrue(again['complete'])
        self.assertEqual(len(client.rows), count)
        for c in client.rows:
            if c['name'] not in profiles.HOSTED and c['source'] == 'bundled':
                self.assertTrue(c['enabled'])
        pubmed = next(c for c in client.rows if c['name'] == profiles.PREFIX + 'pubmed')
        self.assertEqual(pubmed['env']['NCBI_EMAIL'], 'test@example.invalid')
        exported = client.run('export_science', self.params)
        self.assertEqual(len(exported['mcpServers']), 24)
        self.assertNotIn('test@example.invalid', json.dumps(exported))
        self.assertIn('ketcher-chemistry', exported['mcpServers'])

    def test_pending_or_failed_replacement_keeps_its_official_service_and_old_config(self):
        client = FakeBridge(str(self.home))
        client.pending = 'csswitch-local-pubmed'
        old = dict(id='local:csswitch-openscience', name='csswitch-openscience', description='科研数据库 MCP 服务', source='local-stdio', attachedAgents=['OPERON'])
        client.rows.append(old)
        result = client.install_science(self.params)
        self.assertFalse(result['complete'])
        self.assertTrue(next(c for c in client.rows if c['name'] == 'pubmed')['enabled'])
        self.assertIn(old, client.rows)
        self.assertEqual(old['attachedAgents'], ['OPERON'])

    def test_legacy_removal_has_private_recoverable_backup(self):
        client = FakeBridge(str(self.home))
        client.rows.append(dict(id='local:csswitch-openscience', name='csswitch-openscience', description='科研数据库 MCP 服务', source='local-stdio', attachedAgents=['OPERON']))
        result = client.install_science(self.params)
        self.assertTrue(result['complete'])
        self.assertFalse(any(c['name'] == 'csswitch-openscience' for c in client.rows))
        backups = list((self.home / '.csswitch-next/mcp-migration-backups').glob('*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)

    def test_preflight_failure_does_not_add_or_disable_anything(self):
        client = FakeBridge(str(self.home))
        (self.root / 'mcp-servers/bio-tools/lib/mcp_pubmed/server.py').unlink()
        with self.assertRaises(bridge.BridgeError):
            client.install_science(self.params)
        self.assertFalse(any('/local' in e[0] or '/enabled' in e[0] for e in client.events))
