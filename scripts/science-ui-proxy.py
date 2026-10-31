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
import re
from pathlib import Path
import select
import secrets
import socket
import sys
import threading
from socketserver import TCPServer
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'proxy'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from science_local_files import FileActionError, native_json, open_artifact
from routing import load_profile
from csswitch_proxy import PROVIDERS
from science_models import adapt_model_response
from conversation_models import normalize_response
from science_translation import Translator, TranslationError
from science_skill_catalog import augment_catalog, CatalogError


BOOTSTRAP = "(()=>{for(const name of ['zh','files','status']){const s=document.createElement('script');s.src='/__csswitch/'+name+'.js';document.head.append(s)}})();"
SCRIPT_HASH = "'sha256-" + base64.b64encode(hashlib.sha256(BOOTSTRAP.encode()).digest()).decode() + "'"
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade'}


class LoopbackServer(ThreadingHTTPServer):
    # Native Science loads many lazy modules at once on a cold page entry.
    request_queue_size = 128

    def __init__(self, *args, **kwargs):
        self.file_token = secrets.token_urlsafe(32)
        self.translation_token = secrets.token_urlsafe(32)
        self.file_lock = threading.Lock()
        super().__init__(*args, **kwargs)

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
    tag = ('<script src="/__csswitch/model-compat.js"></script><script>' + BOOTSTRAP + '</script>').encode()
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

    def files_enabled(self):
        try:
            return json.loads(self.server.config.read_text()).get('science_local_files') is True
        except (OSError, ValueError, AttributeError):
            return False

    def translator(self):
        # Test servers and the desktop set config after constructing the listener.
        with self.server.file_lock:
            if not hasattr(self.server, 'translator'):
                self.server.translator = Translator(self.server.config)
            return self.server.translator

    def translate_ui(self, origins):
        self.close_connection = True
        try:
            if self.command != 'POST':
                raise TranslationError('请通过 Science 页面翻译。', 405)
            if (self.headers.get('Origin') not in origins or not secrets.compare_digest(
                    self.headers.get('X-CSSwitch-Translation', '').encode(), self.server.translation_token.encode())):
                raise TranslationError('页面验证失效，请刷新页面。', 403)
            if self.translator().state() != (True, True):
                raise TranslationError('联网翻译已关闭。', 403)
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise TranslationError('翻译请求格式无效。', 415)
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 20000:
                raise TranslationError('翻译请求过大。', 413)
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or set(data) != {'texts'}:
                raise TranslationError('翻译请求格式无效。', 400)
            native_json(self.server.upstream_port, self.headers, '/api/me')
            result = self.translator().translate(data['texts'])
            return self.local_reply(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
        except FileActionError as error:
            return self.local_reply(error.status, json.dumps({'error':str(error)}).encode(), 'application/json')
        except TranslationError as error:
            return self.local_reply(error.status, json.dumps({'error':str(error), 'retryAfter':error.retry_after}, ensure_ascii=False).encode(), 'application/json')
        except (OSError, ValueError, TypeError):
            return self.local_reply(400, b'{"error":"Invalid translation request","retryAfter":60}', 'application/json')

    def file_action(self, origins):
        # Do not leave rejected request bodies on a keep-alive connection.
        self.close_connection = True
        try:
            if self.command != 'POST':
                raise FileActionError('请通过文件菜单操作。', 405)
            if (self.headers.get('Origin') not in origins or
                not secrets.compare_digest(self.headers.get('X-CSSwitch-Files', '').encode(), self.server.file_token.encode())):
                raise FileActionError('页面验证失效，请刷新 Science 页面。', 403)
            if not self.files_enabled():
                raise FileActionError('请先在设置 → 通用中开启本地文件插件。', 403)
            if self.headers.get_content_type() != 'application/json' or self.headers.get('Transfer-Encoding'):
                raise FileActionError('不支持的文件请求。', 415)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError()
                payload = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError):
                raise FileActionError('文件请求格式无效。')
            if not self.server.file_lock.acquire(blocking=False):
                raise FileActionError('正在打开文件，请稍候。', 409)
            try:
                result = open_artifact(payload,
                    lambda endpoint: native_json(self.server.upstream_port, self.headers, endpoint), self.files_enabled)
            finally:
                self.server.file_lock.release()
            return self.local_reply(200, json.dumps(result, ensure_ascii=False).encode(), 'application/json')
        except FileActionError as error:
            return self.local_reply(error.status, json.dumps({'error': str(error)}, ensure_ascii=False).encode(), 'application/json')

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
                enabled, online = self.translator().state()
            except (OSError, ValueError, AttributeError):
                enabled = online = False
            data = {'enabled':enabled, 'online':enabled and online,
                    'translationToken':self.server.translation_token if enabled and online else None,
                    'translationRevision':self.translator().revision()}
            return self.local_reply(200, json.dumps(data).encode(), 'application/json')
        if self.path == '/__csswitch/zh/cache':
            return self.local_reply(200, json.dumps({'translations':self.translator().cache(),
                'revision':self.translator().revision()}, ensure_ascii=False).encode(), 'application/json')
        if self.path == '/__csswitch/zh/translate':
            return self.translate_ui(origins)
        if self.path == '/__csswitch/zh.js':
            return self.local_reply(200, Path(__file__).with_name('science-zh.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/__csswitch/files.js':
            return self.local_reply(200, Path(__file__).with_name('science-files.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/__csswitch/model-compat.js':
            choices = {}
            try:
                cfg = json.loads(self.server.config.read_text())
                if cfg.get('mode', 'proxy') == 'proxy':
                    models = load_profile(str(self.server.config), PROVIDERS)['prov']['native_models']
                    for model in sorted(models, key=lambda m: bool(m.get('overflow'))):
                        choices.setdefault(model['family'], model['id'])
            except (OSError, ValueError, KeyError, TypeError):
                pass
            script = Path(__file__).with_name('science-model-compat.js').read_text()
            return self.local_reply(200, script.replace('__CSSWITCH_NATIVE_IDS__', json.dumps(choices)).encode(), 'text/javascript; charset=utf-8')
        if self.path == '/__csswitch/status.js':
            return self.local_reply(200, Path(__file__).with_name('science-status.js').read_bytes(), 'text/javascript; charset=utf-8')
        if self.path == '/__csswitch/status/config':
            try:
                enabled = json.loads(self.server.config.read_text()).get('mode', 'proxy') == 'proxy'
            except (OSError, ValueError, AttributeError):
                enabled = False
            return self.local_reply(200, json.dumps({'enabled': enabled}).encode(), 'application/json')
        if self.path == '/__csswitch/models.js' or self.path.startswith('/__csswitch/models/'):
            self.close_connection = True
            return self.local_reply(410, json.dumps({'error': '模型映射已改由 CSSwitch 统一管理，请刷新页面。'}, ensure_ascii=False).encode(), 'application/json')
        if self.path == '/__csswitch/files/config':
            enabled = self.files_enabled()
            return self.local_reply(200, json.dumps({'enabled': enabled, 'token': self.server.file_token if enabled else None}).encode(), 'application/json')
        if self.path == '/__csswitch/files/action':
            return self.file_action(origins)
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
            asset_path = urlsplit(self.path).path
            native_script = bool(re.fullmatch(r'/assets/[A-Za-z0-9_.-]+\.js', asset_path))
            if native_script:
                for key in list(headers):
                    if key.lower() in {'if-none-match', 'if-modified-since'}:
                        headers.pop(key)
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
            is_models = self.command == 'GET' and urlsplit(self.path).path == '/api/models' and res.status == 200 and 'application/json' in res.getheader('Content-Type', '') and not res.getheader('Content-Encoding')
            path = urlsplit(self.path).path
            selection_path = path in {'/api/frames', '/api/preferences/carried', '/api/preferences/reviewer-model'} or re.fullmatch(r'/api/frames/[A-Za-z0-9_-]+(?:/trace-shallow)?', path)
            is_selection = self.command == 'GET' and selection_path and res.status == 200 and 'application/json' in res.getheader('Content-Type', '') and not res.getheader('Content-Encoding')
            is_skills = self.command == 'GET' and path == '/api/skills/catalog' and res.status == 200 and 'application/json' in res.getheader('Content-Type', '') and not res.getheader('Content-Encoding')
            is_json = is_models or is_selection or is_skills
            is_script = self.command == 'GET' and native_script and res.status == 200 and not res.getheader('Content-Encoding') and 'javascript' in res.getheader('Content-Type', '')
            payload = inject_html(res.read()) if is_html else res.read() if is_json or is_script else None
            if is_script:
                self.translator().observe_asset(payload)
            if is_json:
                try:
                    if is_skills:
                        data = augment_catalog(json.loads(payload), self.server.config, self.server.upstream_port,
                                               lambda endpoint: native_json(self.server.upstream_port, self.headers, endpoint))
                        payload = json.dumps(data, ensure_ascii=False).encode()
                    elif json.loads(self.server.config.read_text()).get('mode', 'proxy') == 'proxy':
                        route = load_profile(str(self.server.config), PROVIDERS)
                        data = adapt_model_response(json.loads(payload), route['prov']) if is_models else normalize_response(json.loads(payload), path, route['prov']['native_models'])
                        payload = json.dumps(data, ensure_ascii=False).encode()
                except (OSError, ValueError, TypeError, KeyError, AttributeError, FileActionError, CatalogError):
                    pass  # Preserve the native response when local configuration is unavailable.
            self.send_response(res.status)
            sent = True
            for k, v in res.getheaders():
                key = k.lower()
                if key in HOP or ((is_html or is_json or is_script) and key in {'content-length', 'etag', 'content-md5', 'cache-control'}):
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
            if is_html or is_json or is_script:
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
    parser.add_argument('--port', type=int, default=0, help='Loopback entry port; use 0 to allocate one')
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--ready', type=Path, required=True)
    args = parser.parse_args()
    server = LoopbackServer(('127.0.0.1', args.port), Handler)
    server.daemon_threads = True
    server.upstream_port, server.config = args.upstream_port, args.config
    # The parent chooses a unique private readiness file. Never replace a symlink.
    fd = os.open(args.ready, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'port': server.server_port}, out)
    server.serve_forever()


if __name__ == '__main__':
    main()
