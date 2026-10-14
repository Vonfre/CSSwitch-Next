"""Offline stdio + HTTPS CONNECT contract test for the generated MCP bundle."""
import json
import os
from pathlib import Path
import select
import shutil
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Proxy(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_CONNECT(self):
        self.server.requests.append(self.path)
        if self.path not in ('rest.uniprot.org:443', 'eutils.ncbi.nlm.nih.gov:443'):
            self.send_error(403)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.flush()
        tls = self.server.context.wrap_socket(self.connection, server_side=True)
        tls.settimeout(10)
        stream = tls.makefile('rb')
        try:
            line = stream.readline().decode()
            path = line.split()[1]
            while stream.readline().strip():
                pass
            headers = ''
            status = 200
            if 'redirect' in path:
                status = 302
                headers = 'Location: http://127.0.0.1/private\r\n'
                payload = {}
            elif 'outage' in path:
                status = 503
                headers = 'Retry-After: 1200\r\n'
                payload = {'message': 'unavailable'}
            elif 'format=fasta' in path:
                payload = '>P04637\nMEEPQSDPSV\n'
            elif 'esearch.fcgi' in path:
                payload = {'esearchresult': {'idlist': ['123'], 'count': '1'}}
            elif 'esummary.fcgi' in path:
                payload = {'result': {'uids': ['123'], '123': {'uid': '123', 'title': 'Fixture paper', 'authors': []}}}
            else:
                payload = {'primaryAccession': 'P04637', 'genes': [{'geneName': {'value': 'TP53'}}]}
            body = (payload if isinstance(payload, str) else json.dumps(payload)).encode()
            content_type = 'text/plain' if isinstance(payload, str) else 'application/json'
            tls.sendall((f'HTTP/1.1 {status} Fixture\r\nContent-Type: {content_type}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n' + headers + '\r\n').encode() + body)
        finally:
            stream.close()
            tls.close()


@unittest.skipUnless((Path(__file__).parents[1] / 'openscience-mcp/server.mjs').is_file(), 'Legacy OpenScience bundle was removed')
class McpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='csswitch-mcp-test-')
        root = Path(cls.temp.name)
        (root / 'cert.cnf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=rest.uniprot.org\n[ext]\nsubjectAltName=DNS:rest.uniprot.org,DNS:eutils.ncbi.nlm.nih.gov\nbasicConstraints=CA:TRUE\n')
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-config', str(root / 'cert.cnf'), '-keyout', str(root / 'key.pem'), '-out', str(root / 'cert.pem')], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        cls.proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
        cls.proxy.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        cls.proxy.context.load_cert_chain(root / 'cert.pem', root / 'key.pem')
        cls.proxy.requests = []
        cls.thread = threading.Thread(target=cls.proxy.serve_forever, daemon=True)
        cls.thread.start()
        env = dict(os.environ, HTTPS_PROXY=f'http://127.0.0.1:{cls.proxy.server_port}', https_proxy=f'http://127.0.0.1:{cls.proxy.server_port}', NO_PROXY='127.0.0.1', no_proxy='127.0.0.1', NODE_EXTRA_CA_CERTS=str(root / 'cert.pem'))
        env.pop('BIOGRID_ACCESS_KEY', None)
        script = Path(__file__).parents[1] / 'openscience-mcp/server.mjs'
        cls.process = subprocess.Popen([shutil.which('node'), str(script)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, env=env)
        cls.counter = 0

    @classmethod
    def tearDownClass(cls):
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.process.stdin.close()
        cls.process.stdout.close()
        cls.proxy.shutdown()
        cls.proxy.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def rpc(self, method, params):
        type(self).counter += 1
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': self.counter, 'method': method, 'params': params}) + '\n')
        self.process.stdin.flush()
        if not select.select([self.process.stdout], [], [], 12)[0]:
            self.fail('MCP did not answer within 12 seconds')
        data = json.loads(self.process.stdout.readline())
        self.assertEqual(data['id'], self.counter)
        return data['result']

    def tool(self, name, arguments):
        result = self.rpc('tools/call', {'name': name, 'arguments': arguments})
        return result['isError'], json.loads(result['content'][0]['text'])

    def test_initialize_roster_and_catalog(self):
        self.assertIn('tools', self.rpc('initialize', {'protocolVersion': '2025-03-26'})['capabilities'])
        tools = self.rpc('tools/list', {})['tools']
        self.assertEqual(len(tools), 3)
        self.assertTrue(all(t['annotations']['readOnlyHint'] for t in tools))
        error, value = self.tool('science_list_dbs', {})
        self.assertFalse(error)
        self.assertEqual(len(value['databases']), 43)

    def test_proxy_search_and_fetch_use_adapters(self):
        error, value = self.tool('science_search', {'db': 'pubmed', 'query': 'fixture', 'limit': 2})
        self.assertFalse(error)
        self.assertEqual(value['results'][0]['title'], 'Fixture paper')
        error, value = self.tool('science_fetch', {'db': 'uniprot', 'id': 'P04637'})
        self.assertFalse(error)
        self.assertTrue(value['found'])
        self.assertEqual(value['record']['primaryAccession'], 'P04637')
        self.assertIn('rest.uniprot.org:443', self.proxy.requests)

    def test_file_format_and_invalid_parameters(self):
        error, value = self.tool('science_fetch', {'db': 'uniprot', 'id': 'P04637', 'format': 'fasta'})
        self.assertFalse(error)
        self.assertIn('MEEPQ', value['file']['body'])
        for tool, args in [('science_search', {'db': 'pubmed', 'query': 'x', 'limit': 0}), ('science_fetch', {'db': 'uniprot', 'id': 'x', 'format': 'exe'}), ('science_fetch', {'db': 'unknown', 'id': 'x'})]:
            self.assertTrue(self.tool(tool, args)[0])

    def test_source_failure_and_redirect_do_not_become_empty_results(self):
        error, value = self.tool('science_fetch', {'db': 'uniprot', 'id': 'outage'})
        self.assertTrue(error)
        self.assertEqual(value['status'], 503)
        self.assertTrue(self.tool('science_fetch', {'db': 'uniprot', 'id': 'redirect'})[0])
        self.assertTrue(self.tool('science_search', {'db': 'biogrid', 'query': 'TP53'})[0])
        self.assertNotIn('127.0.0.1:443', self.proxy.requests)


if __name__ == '__main__':
    unittest.main()
