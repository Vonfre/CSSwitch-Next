#!/usr/bin/env python3
"""Loopback-only Science page entry point with an optional local UI dictionary.

The native daemon, authentication, CSRF checks and WebSocket protocol stay intact.
Never log request paths: the setup URL carries a one-use nonce.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import select
import socket
from socketserver import TCPServer
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

BOOTSTRAP = "(()=>{const s=document.createElement('script');s.src='/__csswitch/zh.js';document.head.append(s)})();"
SCRIPT_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(BOOTSTRAP.encode()).digest()).decode() + "'"
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


class LoopbackServer(ThreadingHTTPServer):
    def server_bind(self):
        # The listener always uses a numeric loopback address. HTTPServer's
        # reverse hostname lookup adds no value and can stall before readiness.
        TCPServer.server_bind(self)
        self.server_name = 'localhost'
        self.server_port = self.server_address[1]


class UpgradeResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(sock, *args, **kwargs)
        self.fp.close()
        # Avoid swallowing the first WebSocket frame into an HTTP header buffer.
        self.fp = sock.makefile('rb', buffering=0)


def inject_html(body):
    tag = ('<script>' + BOOTSTRAP + '</script>').encode()
    marker = body.lower().rfind(b'</body>')
    return body[:marker] + tag + body[marker:] if marker >= 0 else body + tag


def script_policy(value):
    # Respect every other directive; add only our local script and bootstrap hash.
    directives = [d.strip() for d in value.split(';') if d.strip()]
    for key in ('script-src', 'script-src-elem'):
        i = next((i for i, d in enumerate(directives) if d.split()[0].lower() == key), None)
        if i is not None:
            directives[i] = ' '.join(t for t in directives[i].split() if t != "'none'") + " 'self' " + SCRIPT_HASH
        elif key == 'script-src':
            default = next((d.split()[1:] for d in directives if d.startswith('default-src ')), None)
            if default is not None:
                directives.append(' '.join([key, *[t for t in default if t != "'none'"], "'self'", SCRIPT_HASH]))
    return '; '.join(directives)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        pass

    def local_reply(self, status, body=b'', kind='text/plain; charset=utf-8'):
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def handle_request(self):
        # A running 0.7.2 desktop still probes /health without the port in Host.
        # Permit only this empty loopback readiness response, so replacing the
        # bundled script repairs the next open without restarting research.
        if self.path == '/health' and self.headers.get('Host') == '127.0.0.1' and self.headers.get('Origin') is None:
            return self.local_reply(200, b'{}', 'application/json')
        origins = {f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'}
        hosts = {o.split('//')[1] for o in origins}
        if self.headers.get('Host') not in hosts or (self.headers.get('Origin') and self.headers['Origin'] not in origins):
            self.close_connection = True
            return self.local_reply(403)
        if not self.path.startswith('/') or self.path.startswith('//') or urlsplit(self.path).netloc:
            return self.local_reply(400)
        if self.path == '/__csswitch/health':
            return self.local_reply(200, b'{}', 'application/json')
        if self.path == '/__csswitch/config':
            try:
                cfg = json.loads(self.server.config.read_text())
                enabled = cfg.get('science_zh', False) is True
            except (OSError, ValueError):
                enabled = False
            return self.local_reply(200, json.dumps({'enabled': enabled}).encode(), 'application/json')
        if self.path == '/__csswitch/zh.js':
            return self.local_reply(200, Path(__file__).with_name('science-zh.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path.startswith('/__csswitch/'):
            return self.local_reply(404)
        conn = http.client.HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=90)
        upgraded = self.headers.get('Upgrade', '').lower() == 'websocket'
        if upgraded:
            conn.response_class = UpgradeResponse
        sent = False
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > 256 * 1024 * 1024 or self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return self.local_reply(413)
            body = self.rfile.read(length) if length else None
            base = f'http://127.0.0.1:{self.server.upstream_port}'
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP | {'host', 'accept-encoding'}}
            headers['Host'] = base.split('//')[1]
            headers['Accept-Encoding'] = 'identity'
            if self.headers.get('Origin'):
                headers['Origin'] = base
            if self.headers.get('Referer'):
                u = urlsplit(self.headers['Referer'])
                if f'{u.scheme}://{u.netloc}' in origins:
                    headers['Referer'] = base + u.path + ('?' + u.query if u.query else '')
                else:
                    headers.pop('Referer', None)
            if upgraded:
                headers.update(Connection='Upgrade', Upgrade='websocket')
            conn.request(self.command, self.path, body, headers)
            res = conn.getresponse()
            is_html = self.command != 'HEAD' and 'text/html' in res.getheader('Content-Type', '') and res.status == 200 and not res.getheader('Content-Encoding')
            payload = inject_html(res.read()) if is_html else None
            self.send_response(res.status)
            sent = True
            for k, v in res.getheaders():
                key = k.lower()
                if key in HOP or (is_html and key in {'content-length', 'etag', 'content-md5'}):
                    continue
                if key == 'location' and v.startswith(base + '/'):
                    v = f'http://{self.headers["Host"]}' + v[len(base):]
                if is_html and key == 'content-security-policy':
                    v = script_policy(v)
                self.send_header(k, v)
            if upgraded and res.status == 101:
                self.send_header('Connection', 'Upgrade')
                self.send_header('Upgrade', 'websocket')
            else:
                self.send_header('Connection', 'close')
            if is_html:
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.flush()
            self.close_connection = True
            if upgraded and res.status == 101:
                # HTTPResponse owns the buffered socket file after a 101.
                # Keep it alive while raw protocol frames flow in both directions.
                upstream = conn.sock
                if upstream is None:
                    return
                upstream.settimeout(None)
                self.connection.settimeout(None)
                while True:
                    ready, _, _ = select.select([self.connection, upstream], [], [], 60)
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (upstream if source is self.connection else self.connection).sendall(data)
            elif payload is not None:
                self.wfile.write(payload)
            elif self.command != 'HEAD':
                while True:
                    data = res.read1(65536)
                    if not data:
                        break
                    self.wfile.write(data)
                    self.wfile.flush()
        except (OSError, ValueError, http.client.HTTPException):
            self.close_connection = True
            if not sent:
                self.local_reply(502, 'Science 服务不可用，请在 CSSwitch 中检查运行状态。'.encode())
        finally:
            conn.close()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = handle_request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream-port', type=int, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--ready', type=Path, required=True)
    args = parser.parse_args()
    server = LoopbackServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.upstream_port, server.config = args.upstream_port, args.config
    # The parent chooses a unique private readiness file. Never replace a symlink.
    fd = os.open(args.ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'port': server.server_port}, out)
    server.serve_forever()


if __name__ == '__main__':
    main()
