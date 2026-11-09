#!/usr/bin/env python3
"""CSSwitch's independent subscription gateway and PKCE login callback."""
import argparse
import base64
from email.utils import parsedate_to_datetime
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subscription_store import Store, SubscriptionError

# Public native-client parameters verified in installed Science 0.1.72.
CLIENT_ID = 'a135f8ff-7c18-4143-b5c4-78cf58d0ed17'
TOKEN_URL = 'https://platform.claude.com/v1/oauth/token'
PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile'
SCOPES = 'user:inference user:file_upload user:profile user:mcp_servers user:plugins'
USER_AGENT = 'CSSwitch/0.8.30'


class OAuthLoginError(SubscriptionError):
    def __init__(self, stage, kind, message):
        super().__init__(message)
        self.stage, self.kind = stage, kind


def oauth_failure(exc, stage):
    """Never expose upstream bodies, callback codes, state or tokens to the UI."""
    if isinstance(exc, OAuthLoginError):
        return exc
    label = {'exchange': '兑换授权', 'profile': '读取账号信息', 'save': '保存账号'}.get(stage, '完成登录')
    if isinstance(exc, urllib.error.HTTPError):
        kind = 'http_' + str(exc.code)
        detail = ''
        try:
            payload = json.loads(exc.read(8192))
            if payload.get('error_code') == 1010:
                kind, detail = 'client_rejected', '服务端拦截了客户端请求，未完成令牌交换。'
            elif payload.get('error') == 'invalid_grant':
                kind, detail = 'invalid_grant', '授权码无效、已使用或过期，请重新添加账号。'
        except Exception:
            pass
        finally:
            exc.close()
        return OAuthLoginError(stage, kind, f'{label}失败（HTTP {exc.code}）。' + (detail or '请重新添加账号；若仍失败，请反馈此阶段和状态码。'))
    reason = getattr(exc, 'reason', exc)
    if isinstance(reason, ssl.SSLError):
        return OAuthLoginError(stage, 'tls_error', f'{label}时 HTTPS 证书验证失败，请检查本机网络或证书配置。')
    if isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError)):
        return OAuthLoginError(stage, 'network_error', f'{label}时网络连接失败或超时，请检查网络后重新添加账号。')
    if stage == 'save':
        return OAuthLoginError(stage, 'storage_error', '授权已返回，但账号未能保存到本机。请检查订阅目录权限和磁盘空间后重新添加账号。')
    return OAuthLoginError(stage, 'invalid_response', f'{label}返回的账号信息不完整或格式不兼容，请重新添加账号。')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def request_json(url, form=None, token=None):
    # Identify this client explicitly. Python's default urllib User-Agent is
    # rejected by the OAuth edge before the authorization code is inspected.
    headers = {'Accept': 'application/json', 'User-Agent': USER_AGENT}
    data = None
    if form is not None:
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        data = urllib.parse.urlencode(form).encode()
    if token:
        if not isinstance(token, str) or any(c in token for c in '\r\n'):
            raise SubscriptionError('登录令牌格式无效。')
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
        return json.load(response)


def retry_delay(headers):
    value = headers.get('Retry-After', '')
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = parsedate_to_datetime(value).timestamp() - time.time()
        except Exception:
            delay = 60
    return max(1, min(delay, 7 * 86400))


class Gateway(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, store, upstream='https://api.anthropic.com'):
        self.store = store
        self.secret = store.gateway()['secret']
        self.upstream = upstream
        self.pending = {}
        self.lock = threading.RLock()
        self.refresh_locks = {}
        self.active = 0
        self.stopping = False
        self.requests = 0
        self.last_account = ''
        self.last_inference = None
        # Tests inject a loopback upstream directly into this class, never via CLI.
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({})) if upstream.startswith('http://127.0.0.1:') else urllib.request.build_opener(NoRedirect())
        super().__init__(address, Handler)

    def completed(self, ident, revision, route, started_at):
        # Catalog and token-count requests are not evidence of model inference.
        # Keep only routing metadata, never prompts, responses or credentials.
        with self.lock:
            self.last_account = ident
            if route == '/v1/messages':
                self.last_inference = {'account_id': ident, 'revision': revision,
                                       'started_at': started_at, 'completed_at': time.time()}

    def begin_login(self):
        verifier = secrets.token_urlsafe(48)
        state = secrets.token_urlsafe(32)
        redirect = f'http://localhost:{self.server_port}/oauth/code/callback'
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        with self.lock:
            self.pending = {s: v for s, v in self.pending.items() if v['expires'] > time.time()}
            if len(self.pending) >= 8:
                raise SubscriptionError('已有登录等待完成，请稍后再试。')
            self.pending[state] = {'verifier': verifier, 'redirect': redirect, 'expires': time.time() + 600}
        self.store.set_login_status('pending', 'authorize', '已打开官方授权页，等待授权完成。')
        return 'https://claude.ai/oauth/authorize?' + urllib.parse.urlencode({
            'response_type': 'code', 'client_id': CLIENT_ID, 'redirect_uri': redirect,
            'scope': SCOPES, 'state': state, 'code_challenge': challenge, 'code_challenge_method': 'S256'})

    def finish_login(self, state, code, error=None):
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or pending['expires'] < time.time() or not code:
            if pending and pending['expires'] >= time.time() and error:
                raise OAuthLoginError('authorize', 'authorization_denied', '官方授权未完成或已取消，请返回 CSSwitch 重新添加账号。')
            raise OAuthLoginError('callback', 'state_expired', '此登录链接已处理、过期或来自旧网关。请返回 CSSwitch 重新点击「添加账号」，不要刷新旧回调页。')
        stage = 'exchange'
        try:
            token = request_json(TOKEN_URL, {'grant_type': 'authorization_code', 'code': code,
                'code_verifier': pending['verifier'], 'state': state, 'redirect_uri': pending['redirect'], 'client_id': CLIENT_ID})
            scopes = token.get('scope', SCOPES)
            if not isinstance(scopes, str) or 'user:inference' not in scopes.split():
                raise OAuthLoginError('exchange', 'missing_scope', '此登录未授权模型推理，请重新添加账号并完成授权。')
            stage = 'profile'
            # Native OAuth can include a complete identity in the token response.
            # Optional profile enrichment must not discard an otherwise valid login.
            account, org = token.get('account') or {}, token.get('organization') or {}
            profile = {}
            if not (account.get('uuid') and (account.get('email_address') or account.get('email')) and org.get('organization_type')):
                try:
                    profile = request_json(PROFILE_URL, token=token['access_token'])
                except Exception:
                    if not (account.get('uuid') and (account.get('email_address') or account.get('email'))):
                        raise
            account = {**account, **(profile.get('account') or (profile if profile.get('uuid') else {}))}
            org = {**org, **(profile.get('organization') or {})}
            if not account.get('uuid') or not (account.get('email_address') or account.get('email')):
                raise OAuthLoginError('profile', 'missing_identity', '官方响应缺少账号 ID 或邮箱，未保存账号。请重新授权。')
            expires_at = time.time() + float(token['expires_in'])
            stage = 'save'
            return self.store.add({'account_uuid': account['uuid'], 'email': account.get('email_address') or account.get('email'),
                'org_uuid': org.get('uuid'), 'access_token': token['access_token'], 'refresh_token': token.get('refresh_token'),
                'expires_at': expires_at, 'scopes': scopes,
                'subscription_type': {'claude_pro': 'pro', 'claude_max': 'max', 'claude_team': 'team', 'claude_enterprise': 'enterprise'}.get(org.get('organization_type'), '未知')})
        except Exception as exc:
            raise oauth_failure(exc, stage) from None

    def token(self, ident):
        with self.lock:
            lock = self.refresh_locks.setdefault(ident, threading.Lock())
        with lock:
            state = self.store.snapshot()
            account = state['accounts'].get(ident)
            if account is None:
                raise SubscriptionError('账号已删除，请重新选择账号。')
            if account.get('native_owned'):
                if not state.get('hot_mode'):
                    raise SubscriptionError('请先启用免重启切换。')
                from science_hot_switch import native_token
                from subscription_usage import UsageError
                try:
                    return native_token(self.store, ident, account)
                except UsageError as error:
                    raise SubscriptionError(error.message) from None
            if account.get('expires_at', 0) > time.time() + 60:
                return account['access_token']
            if not account.get('refresh_token'):
                raise SubscriptionError('账号登录已过期。')
            refreshed = request_json(TOKEN_URL, {'grant_type': 'refresh_token', 'refresh_token': account['refresh_token'], 'client_id': CLIENT_ID})
            if not isinstance(refreshed.get('access_token'), str) or not refreshed['access_token'] or any(c in refreshed['access_token'] for c in '\r\n'):
                raise SubscriptionError('账号刷新失败，请重新登录。')
            if (refreshed.get('account') or {}).get('uuid', account['account_uuid']) != account['account_uuid']:
                raise SubscriptionError('刷新响应的账号身份不一致。')
            with self.store.locked():
                current = self.store.read()
                # A concurrent new login wins over a refresh of an old credential.
                saved = current['accounts'].get(ident)
                if saved and saved.get('refresh_token') == account['refresh_token']:
                    saved.update(access_token=refreshed['access_token'], refresh_token=refreshed.get('refresh_token') or account['refresh_token'],
                                 expires_at=time.time() + float(refreshed['expires_in']), lapsed=False)
                    self.store.write(current)
                    return saved['access_token']
                if saved:
                    return saved['access_token']
                raise SubscriptionError('账号已移除。')

    def candidates(self):
        state = self.store.snapshot()
        return state['revision'], self.store.candidate_ids(state)


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_):
        pass  # Callback URLs and headers contain credentials. Never log them.

    def end_headers(self):
        super().end_headers()
        self.committed = True

    def send_json(self, code, value):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def error(self, code, text):
        self.send_json(code, {'type': 'error', 'error': {'type': 'api_error', 'message': text}})

    def login_page(self, ok, message):
        title = '账号已添加' if ok else '登录未完成'
        body = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width,initial-scale=1">'
                '<title>CSSwitch · ' + title + '</title><style>'
                'body{font:16px/1.8 system-ui;background:#f6f6f6;color:#222;margin:0;padding:8vh 24px}'
                'main{max-width:540px;margin:auto;background:white;border:1px solid #ddd;border-radius:16px;padding:32px}'
                'h1{font-size:26px}p{overflow-wrap:anywhere}.brand{color:#777}</style>'
                '<main><div class="brand">CSSwitch · 订阅账号</div><h1>' + title + '</h1><p>'
                + html.escape(message) + '</p><p>返回 CSSwitch 的「订阅账号」页刷新查看。此页面可以关闭。</p></main></html>').encode()
        self.send_response(200 if ok else 400)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def dispatch(self):
        self.connection.settimeout(120)
        host = self.headers.get('Host', '')
        if host not in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}') or self.headers.get('Origin'):
            self.close_connection = True
            return self.error(403, '仅接受本机客户端请求。')
        path = urllib.parse.urlsplit(self.path)
        if path.path == '/oauth/code/callback' and self.command == 'GET':
            query = urllib.parse.parse_qs(path.query)
            try:
                self.server.finish_login(query.get('state', [''])[0], query.get('code', [''])[0], query.get('error', [None])[0])
                message = '账号已保存到 CSSwitch。请返回「订阅账号」刷新；可继续添加第二个账号。'
                outcome = ('success', 'complete', message, '')
            except Exception as exc:
                failure = oauth_failure(exc, 'callback')
                message = str(failure)
                outcome = ('error', failure.stage, message, failure.kind)
            try:
                self.server.store.set_login_status(*outcome)
            except Exception:
                pass  # Diagnostic persistence must not undo a saved account.
            return self.login_page(outcome[0] == 'success', message)
        prefix = '/' + self.server.secret
        if not path.path.startswith(prefix + '/'):
            self.close_connection = True
            return self.error(403, '本地访问凭据无效。')
        route = path.path[len(prefix):]
        if route == '/health' and self.command == 'GET':
            with self.server.lock:
                return self.send_json(200, {'service': 'csswitch-subscriptions', 'version': USER_AGENT.split('/')[-1], 'pid': os.getpid(),
                    'active_requests': self.server.active, 'requests': self.server.requests, 'last_account': self.server.last_account,
                    'last_inference': self.server.last_inference})
        if route == '/login' and self.command == 'POST':
            return self.send_json(200, {'url': self.server.begin_login()})
        if route == '/shutdown' and self.command == 'POST':
            with self.server.lock:
                if self.server.active:
                    return self.error(409, '网关仍有请求，请稍后重试。')
                self.server.stopping = True
            self.send_json(200, {'stopping': True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        if (self.command, route) not in (('POST', '/v1/messages'), ('POST', '/v1/messages/count_tokens'), ('GET', '/v1/models')):
            self.close_connection = True
            return self.error(404, '不支持此网关路径。')
        body = None
        if self.command == 'POST':
            if self.headers.get('Transfer-Encoding'):
                self.close_connection = True
                return self.error(400, '需要明确的请求长度。')
            try:
                size = int(self.headers.get('Content-Length', '-1'))
                if not 0 <= size <= 64 * 1024 * 1024:
                    raise ValueError()
            except ValueError:
                self.close_connection = True
                return self.error(413, '请求长度无效或超过 64 MB。')
            body = self.rfile.read(size)
            if len(body) != size:
                self.close_connection = True
                return self.error(400, '请求未接收完整。')
        with self.server.lock:
            if self.server.stopping:
                return self.error(503, '网关正在停止。')
            self.server.active += 1
            self.server.requests += 1
        try:
            self.forward(route, body, path.query)
        finally:
            with self.server.lock:
                self.server.active -= 1

    def do_GET(self):
        self.committed = False
        try:
            self.dispatch()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True
        except Exception:
            self.close_connection = True
            if not self.committed:
                self.error(502, '订阅网关未完成请求；请检查账号状态。')

    do_POST = do_GET

    def forward(self, route, body, query):
        started_at = time.time()
        revision, candidates = self.server.candidates()
        if not candidates:
            return self.error(429, '没有可用订阅账号，请登录账号或等待额度恢复。')
        last_status = 503
        for ident in candidates:
            try:
                token = self.server.token(ident)
            except urllib.error.HTTPError as exc:
                # Network failures are not evidence a refresh token was revoked.
                self.server.store.note(ident, revision, 401 if exc.code in (400, 401) else 503, 60)
                continue
            except SubscriptionError:
                self.server.store.note(ident, revision, 401, 60)
                continue
            # Preserve the Science request body byte-for-byte, including history,
            # tool IDs/results, model names, thinking signatures and metadata.
            headers = {k: v for k, v in self.headers.items() if k.lower() in (
                'content-type', 'anthropic-version', 'anthropic-beta', 'x-anthropic-surface', 'user-agent', 'accept')}
            headers['Authorization'] = 'Bearer ' + token
            headers['Accept-Encoding'] = 'identity'
            url = self.server.upstream + route + ('?' + query if query else '')
            req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
            try:
                response = self.server.opener.open(req, timeout=120)
            except urllib.error.HTTPError as exc:
                last_status = exc.code
                # Only explicit rejections before any downstream bytes are sent
                # may change accounts. No network-ambiguity or partial-SSE retry.
                if exc.code in (401, 429, 503, 529):
                    self.server.store.note(ident, revision, exc.code, retry_delay(exc.headers), access_token=token)
                    exc.close()
                    continue
                exc.close()
                return self.error(exc.code if 400 <= exc.code <= 599 else 502, '官方服务拒绝此请求；未重发，请检查模型与账号权限。')
            except (OSError, urllib.error.URLError):
                return self.error(502, '上游连接中断，执行状态不确定；为避免重复请求未自动重发。')
            with response:
                content_type = response.headers.get('Content-Type', 'application/json')
                is_sse = 'text/event-stream' in content_type
                if not is_sse:
                    # Buffer bounded JSON responses to detect truncated HTTP bodies
                    # before telling Science that a request succeeded.
                    content = response.read(64 * 1024 * 1024 + 1)
                    expected = response.headers.get('Content-Length')
                    if len(content) > 64 * 1024 * 1024 or (expected is not None and len(content) != int(expected)):
                        return self.error(502, '上游响应不完整；未在其他账号重放请求。')
                    self.send_response(response.status)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(content)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    self.wfile.write(content)
                    self.server.store.note(ident, revision, 200, access_token=token)
                    self.server.completed(ident, revision, route, started_at)
                    return
                # Header commitment happens once. An in-flight request owns this
                # credential and response even when another process changes mode.
                self.send_response(response.status)
                self.send_header('Content-Type', content_type)
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.close_connection = True
                pending, complete = b'', False
                try:
                    while True:
                        chunk = response.read1(8192)
                        if not chunk:
                            break
                        if is_sse:
                            pending += chunk
                            while b'\n' in pending:
                                line, pending = pending.split(b'\n', 1)
                                if line.startswith(b'data:'):
                                    try:
                                        event = json.loads(line[5:])
                                        complete = complete or event.get('type') == 'message_stop'
                                    except (ValueError, AttributeError):
                                        pass
                            if len(pending) > 8 * 1024 * 1024:
                                raise ValueError('oversized SSE event')
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    if is_sse and not complete:
                        raise ValueError('incomplete SSE')
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception:
                    if is_sse:
                        self.wfile.write(b'\n\nevent: error\ndata: {"type":"error","error":{"type":"api_error","message":"Stream interrupted; not replayed on another account."}}\n\n')
                        self.wfile.flush()
                    return
            self.server.store.note(ident, revision, 200, access_token=token)
            self.server.completed(ident, revision, route, started_at)
            return
        return self.error(last_status, '订阅账号当前均不可用，请检查登录或额度；已保留本机会话。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    store = Store(args.root)
    gateway = store.gateway()
    server = Gateway(('127.0.0.1', gateway['port']), store)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
