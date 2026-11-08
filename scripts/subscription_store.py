"""CSSwitch private, encrypted subscription state. No Science auth files are written."""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import tempfile
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class SubscriptionError(Exception):
    pass


def private_dir(path):
    path = Path(path)
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise SubscriptionError('订阅存储路径不能包含符号链接。')
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def private_read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid() or meta.st_mode & 0o077:
            raise SubscriptionError('订阅配置必须属于当前用户且权限为 0600。')
        return stream.read()


def private_write(path, data):
    path = Path(path)
    if path.is_symlink():
        raise SubscriptionError('拒绝符号链接配置。')
    fd, name = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Store:
    def __init__(self, root):
        self.root = private_dir(root)

    @contextmanager
    def locked(self):
        fd = os.open(self.root / '.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def key(self):
        path = self.root / 'vault.key'
        if not path.exists():
            # Only called inside the store lock.
            private_write(path, secrets.token_bytes(32))
        key = private_read(path)
        if len(key) != 32:
            raise SubscriptionError('订阅密钥文件损坏，未改写账号数据。')
        return key

    def read(self):
        path = self.root / 'accounts.enc'
        if not path.exists():
            return {'accounts': {}, 'mode': 'auto', 'selected': '', 'revision': 0}
        try:
            framed = private_read(path)
            if not framed.startswith(b'CSSUB1:'):
                raise ValueError()
            raw = base64.b64decode(framed[7:], validate=True)
            value = json.loads(AESGCM(self.key()).decrypt(raw[:12], raw[12:], b'csswitch-subscriptions-v1'))
            if not isinstance(value.get('accounts'), dict) or value.get('mode') not in ('auto', 'manual'):
                raise ValueError()
            return value
        except Exception:
            raise SubscriptionError('订阅存储无法解密；未覆盖现有账号。') from None

    def write(self, value):
        nonce = secrets.token_bytes(12)
        data = AESGCM(self.key()).encrypt(nonce, json.dumps(value).encode(), b'csswitch-subscriptions-v1')
        private_write(self.root / 'accounts.enc', b'CSSUB1:' + base64.b64encode(nonce + data))

    def snapshot(self):
        with self.locked():
            return self.read()

    def set_login_status(self, status, stage, message, kind=''):
        # Only application-created, sanitized diagnostics, never upstream text.
        value = {'status': status, 'stage': stage, 'message': message, 'kind': kind, 'at': time.time()}
        with self.locked():
            private_write(self.root / 'login-status.json', json.dumps(value).encode())

    def login_status(self):
        path = self.root / 'login-status.json'
        if not path.exists():
            return None
        value = json.loads(private_read(path))
        return {key: value[key] for key in ('status', 'stage', 'message', 'kind', 'at') if key in value}

    def gateway(self):
        with self.locked():
            path = self.root / 'gateway.json'
            if not path.exists():
                private_write(path, json.dumps({'port': 18993, 'secret': secrets.token_hex(32)}).encode())
            value = json.loads(private_read(path))
            if type(value.get('port')) is not int or not 1024 <= value['port'] <= 65535 or len(value.get('secret', '')) != 64:
                raise SubscriptionError('订阅网关配置无效。')
            return value

    def add(self, account):
        for field in ('account_uuid', 'email', 'access_token'):
            if not isinstance(account.get(field), str) or not account[field]:
                raise SubscriptionError('登录响应缺少账号身份或令牌。')
        if '\n' in account['access_token'] or '\r' in account['access_token']:
            raise SubscriptionError('登录令牌格式无效。')
        ident = hashlib.sha256(account['account_uuid'].encode()).hexdigest()[:24]
        with self.locked():
            state = self.read()
            previous = state['accounts'].get(ident, {})
            state['accounts'][ident] = {**account, 'enabled': previous.get('enabled', True), 'cooldown_until': 0, 'lapsed': False}
            state['selected'] = state['selected'] or ident
            state['revision'] += 1
            self.write(state)
        return ident

    def route(self, ident=None):
        with self.locked():
            state = self.read()
            usable = [k for k, a in state['accounts'].items() if not a.get('lapsed')
                      and (a.get('expires_at', 0) > time.time() or a.get('refresh_token'))]
            if ident is not None and ident not in usable:
                raise SubscriptionError('该账号不存在或需要重新登录。')
            if ident is None and len(usable) < 2:
                raise SubscriptionError('请先添加至少两个有效订阅账号。')
            state['mode'] = 'manual' if ident else 'auto'
            if ident:
                state['selected'] = ident
            for key, account in state['accounts'].items():
                account['enabled'] = key in usable
            state['revision'] += 1
            self.write(state)

    def note(self, ident, revision, status, cooldown=0, access_token=None):
        with self.locked():
            state = self.read()
            if ident not in state['accounts']:
                return
            account = state['accounts'][ident]
            if access_token is not None and account['access_token'] != access_token:
                return  # A new login/refresh supersedes this old request's error.
            account['last_status'] = status
            if cooldown:
                account['cooldown_until'] = time.time() + cooldown
            if status == 401:
                account['lapsed'] = True
            # An old request must not undo a manual change made while it ran.
            if status == 200 and state['revision'] == revision and state['mode'] == 'auto':
                state['selected'] = ident
            self.write(state)

    def public(self):
        state = self.snapshot()
        now = time.time()
        accounts = []
        for ident, account in state['accounts'].items():
            expired = not account.get('native_owned') and account.get('expires_at', 0) <= now and not account.get('refresh_token')
            accounts.append({'id': ident, 'user': account['email'], 'plan': account.get('subscription_type') or '未知',
                             'lapsed': bool(account.get('lapsed') or expired),
                             'on': account.get('enabled', True) and (state['mode'] == 'auto' or state['selected'] == ident),
                             'current': state['selected'] == ident,
                             'cooldown_until': account.get('cooldown_until', 0),
                             'last_status': account.get('last_status'), 'windows': []})
        return {'accounts': accounts, 'mode': state['mode'], 'revision': state['revision'], 'native_mode': bool(state.get('native_mode')),
                'ready_accounts': sum(not a['lapsed'] for a in accounts), 'login': self.login_status()}
