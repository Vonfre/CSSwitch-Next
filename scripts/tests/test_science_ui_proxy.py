import base64
import importlib.util
import json
from pathlib import Path
import socket
import tempfile
import threading
import unittest
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
        if self.path == '/redirect':
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
        self.proxy = ThreadingHTTPServer(('127.0.0.1', 0), ui.Handler)
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
        with self.request('/__csswitch/config') as r: self.assertEqual(json.load(r), {'enabled': True})
        self.proxy.config.write_text('{"science_zh":false}')
        with self.request('/__csswitch/config') as r: self.assertEqual(json.load(r), {'enabled': False})
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


if __name__ == '__main__': unittest.main()
