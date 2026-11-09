"""Official Claude quota reads, with bounded caching and inactive-slot refresh.

Only normalized percentages/times cross IPC. Never send credentials to a proxy
configured as an inference provider, follow redirects, or request a model.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
import time
import urllib.error
import urllib.request

from cryptography.fernet import Fernet
from subscription_store import SubscriptionError, private_read, private_write
from subscription_platform import linked, protect, lock_file
from science_native_switch import NativeSwitcher
from subscription_gateway import CLIENT_ID, TOKEN_URL, NoRedirect

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'
CACHE_SECONDS = 60
WINDOWS = (('five_hour', '5 小时'), ('seven_day', '一周'),
           ('seven_day_fable', '一周 Fable'), ('seven_day_opus', '一周 Opus'), ('seven_day_sonnet', '一周 Sonnet'))


class UsageError(Exception):
    def __init__(self, status, message, retry=60):
        self.status, self.message, self.retry = status, message, retry


def request_json(url, token=None, form=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'CSSwitch/0.8.25',
               'anthropic-beta': 'oauth-2025-04-20'}
    if token:
        if not isinstance(token, str) or any(c in token for c in '\r\n'):
            raise UsageError('credentials_unavailable', '登录凭据不可用，请重新添加账号。')
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if form is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(form).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.build_opener(NoRedirect()).open(req, timeout=12) as response:
        raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('oversized response')
        return json.loads(raw)


def normalize(payload):
    if not isinstance(payload, dict):
        raise ValueError('invalid response')
    payload = dict(payload)
    scoped = set()
    for item in payload.get('limits', []) or []:
        if not isinstance(item, dict) or item.get('kind') != 'weekly_scoped' or item.get('group') != 'weekly':
            continue
        scope = item.get('scope') or {}
        if not isinstance(scope, dict) or scope.get('surface') is not None:
            continue
        model = scope.get('model') or {}
        name = str(model.get('display_name', '')).strip().lower() if isinstance(model, dict) else ''
        key = 'seven_day_' + name
        used = item.get('percent')
        if name in ('fable', 'opus', 'sonnet') and key not in scoped and type(used) in (int, float) and math.isfinite(used) and 0 <= used <= 100:
            payload[key] = {'utilization': used, 'resets_at': item.get('resets_at')}
            scoped.add(key)
    if not any(k in payload for k, _ in WINDOWS):
        raise ValueError('missing windows')
    windows = []
    for key, label in WINDOWS:
        value = payload.get(key)
        used, reset = None, None
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError('invalid window')
            used = value.get('utilization')
            if type(used) not in (float, int) or not math.isfinite(used) or not 0 <= used <= 100:
                raise ValueError('invalid utilization')
            if value.get('resets_at') is not None:
                dt = datetime.fromisoformat(value['resets_at'].replace('Z', '+00:00'))
                if dt.tzinfo is None:
                    raise ValueError('missing timezone')
                reset = dt.timestamp()
        if value is not None or key in ('five_hour', 'seven_day'):
            windows.append({'key': key, 'label': label, 'used_percent': used,
                            'remaining_percent': None if used is None else round(100 - used, 2), 'resets_at': reset})
    return windows


@contextmanager
def try_lock(path):
    if linked(path):
        raise SubscriptionError('拒绝链接锁文件。')
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'r+b') as stream:
        protect(path)
        lock_file(stream.fileno(), blocking=False)
        yield


class UsageService:
    def __init__(self, manager, requester=request_json):
        self.manager, self.store, self.requester = manager, manager.store, requester
        self.native = NativeSwitcher(manager)
        self.path = self.store.root / 'usage.json'

    def cache(self):
        try:
            value = json.loads(private_read(self.path))
            return value if isinstance(value, dict) else {}
        except FileNotFoundError:
            return {}

    def public(self, cache=None):
        cache = self.cache() if cache is None else cache
        now = time.time()
        accounts = self.store.snapshot()['accounts']
        return {ident: {**cache.get(ident, {'status': 'pending', 'windows': []}),
                        'stale': cache.get(ident, {}).get('status') != 'ok' or
                                 now - cache.get(ident, {}).get('fetched_at', 0) >= CACHE_SECONDS}
                for ident in accounts}

    def credential(self, account):
        ident = account['account_uuid']
        active = self.native.active().get('account_uuid') == ident
        if active or account.get('native_owned'):
            path = self.native.token_path(ident) if active else self.native.slots / ident / 'token.enc'
            value = self.native.read_token(path)
            if value.get('account_uuid') != ident or value.get('org_uuid') != account['org_uuid']:
                raise UsageError('credentials_unavailable', '登录缓存身份不匹配，请重新添加账号。')
            return value, path
        return account, None

    def refresh_inactive(self, ident, original):
        # Serialize with switch/delete/recovery and other token refreshes. The
        # running Science always owns its live token; never rotate that here.
        try:
            with try_lock(self.store.root / '.manager.lock'):
                state = self.store.snapshot()
                account = state['accounts'].get(ident)
                if not account:
                    raise UsageError('credentials_unavailable', '账号已移除。')
                if self.native.active().get('account_uuid') == account['account_uuid']:
                    raise UsageError('auth_required', '当前登录凭据已过期，请在 Science 中重新连接或重新添加账号。')
                credential, path = self.credential(account)
                if credential.get('access_token') != original.get('access_token'):
                    return credential
                if not credential.get('refresh_token'):
                    raise UsageError('auth_required', '账号授权已过期，请重新添加账号。')
                # A legacy gateway may still own an untransferred token.
                if path is None and self.manager.health():
                    raise UsageError('busy', '登录服务正在管理此账号，请稍后刷新。')
                result = self.requester(TOKEN_URL, form={'grant_type': 'refresh_token',
                    'refresh_token': credential['refresh_token'], 'client_id': CLIENT_ID})
                access = result.get('access_token')
                refresh = result.get('refresh_token') or credential['refresh_token']
                expires = float(result['expires_in'])
                if not isinstance(access, str) or not access or any(c in access for c in '\r\n') or not isinstance(refresh, str) or not math.isfinite(expires) or expires <= 0:
                    raise ValueError('invalid token response')
                if (result.get('account') or {}).get('uuid', account['account_uuid']) != account['account_uuid']:
                    raise ValueError('identity mismatch')
                if (result.get('organization') or {}).get('uuid', account['org_uuid']) != account['org_uuid']:
                    raise ValueError('organization mismatch')
                updated = {**credential, 'access_token': access, 'refresh_token': refresh}
                if path is not None:
                    updated['token_expires_at'] = datetime.fromtimestamp(time.time() + expires, timezone.utc).isoformat()
                    private_write(path, Fernet(self.native.key()).encrypt(json.dumps(updated).encode()))
                    (path.parent / 'refresh-journal').unlink(missing_ok=True)
                else:
                    with self.store.locked():
                        latest = self.store.read()
                        saved = latest['accounts'].get(ident)
                        if not saved or saved.get('refresh_token') != credential['refresh_token']:
                            raise UsageError('busy', '账号授权已更新，请重新刷新。')
                        saved.update(access_token=access, refresh_token=refresh, expires_at=time.time() + expires, lapsed=False)
                        self.store.write(latest)
                return updated
        except BlockingIOError:
            raise UsageError('busy', '正在切换或更新登录，请稍后刷新。') from None

    def query(self, ident, account, previous):
        now = time.time()
        if now < previous.get('retry_at', 0):
            return previous
        result = {**previous, 'checked_at': now, 'retry_at': now + CACHE_SECONDS}
        try:
            credential, _ = self.credential(account)
            access = credential.get('access_token')
            if not access:
                raise UsageError('credentials_unavailable', '登录缓存不可用，请重新添加账号。')
            try:
                payload = self.requester(USAGE_URL, token=access)
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    raise
                error.close()
                credential = self.refresh_inactive(ident, credential)
                payload = self.requester(USAGE_URL, token=credential['access_token'])
            result.update(status='ok', message='', windows=normalize(payload), fetched_at=time.time())
        except UsageError as error:
            result.update(status=error.status, message=error.message, retry_at=now + error.retry)
        except urllib.error.HTTPError as error:
            status, message = 'http_error', f'官方额度接口暂不可用（HTTP {error.code}）。'
            if error.code in (400, 401):
                status, message = 'auth_required', '账号授权已过期，请重新添加账号。'
                result['retry_at'] = now + 300
            elif error.code == 403:
                status, message = 'forbidden', '官方拒绝额度查询（HTTP 403），请稍后重试或重新授权。'
            elif error.code == 429:
                status, message = 'rate_limited', '官方查询限频，请稍后手动刷新。'
                try: delay = min(900, max(60, float(error.headers.get('Retry-After', 120))))
                except (TypeError, ValueError): delay = 120
                result['retry_at'] = now + delay
            result.update(status=status, message=message)
            error.close()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            result.update(status='network_error', message='额度查询网络连接失败或超时，请稍后重试。')
        except SubscriptionError:
            result.update(status='credentials_unavailable', message='无法读取此账号的登录缓存，请重新添加账号。')
        except Exception:
            result.update(status='invalid_response', message='额度数据格式暂不支持，请稍后重试。')
        result.setdefault('windows', [])
        return result

    def refresh(self, ident=None, force=False):
        try:
            with try_lock(self.store.root / '.usage.lock'):
                cached = self.cache()
                snapshot = self.store.snapshot()
                accounts = snapshot['accounts']
                if cached.get('_revision') != snapshot['revision']:
                    cached = {key: {**value, 'retry_at': value.get('retry_at', 0) if value.get('status') == 'rate_limited' else 0} for key, value in cached.items() if key in accounts}
                if ident is not None and ident not in accounts:
                    raise SubscriptionError('账号不存在。')
                targets = {ident: accounts[ident]} if ident else accounts
                if force:
                    for key in targets:
                        if cached.get(key, {}).get('status') == 'ok':
                            cached[key] = {**cached[key], 'retry_at': 0}
                updated = {key: value for key, value in cached.items() if key in accounts}
                with ThreadPoolExecutor(max_workers=4) as pool:
                    pending = {ident: pool.submit(self.query, ident, account, cached.get(ident, {}))
                               for ident, account in targets.items()}
                    updated.update({ident: future.result() for ident, future in pending.items()})
                # Remove accounts deleted while network requests were in flight.
                latest = self.store.snapshot()
                current = latest['accounts']
                updated = {ident: value for ident, value in updated.items() if ident in current}
                # If authorization changed in flight, retry on the next read.
                private_write(self.path, json.dumps({**updated, '_revision': snapshot['revision']}).encode())
                return {'accounts': self.public(updated), 'refresh_seconds': CACHE_SECONDS}
        except BlockingIOError:
            return {'accounts': self.public(), 'refresh_seconds': CACHE_SECONDS}
