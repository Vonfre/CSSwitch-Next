"""Offline native account handoff for supported Science credential layouts.

Credentials have one refresh owner: Science. Each switch checkpoints the stopped
instance, reuses a shared local workspace, and verifies native /auth/status.
No login labels, account UUIDs, or organization identities are fabricated.
"""
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import http.cookiejar
import json
import os
import plistlib
from pathlib import Path
import re
import signal
import shutil
import sqlite3
import stat
import subprocess
import time
import urllib.parse
import urllib.request
import uuid

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from subscription_store import SubscriptionError, private_dir, private_read, private_write
from subscription_platform import WINDOWS, linked, close_science_windows
from science_history_shared import SharedHistory


def checked_uuid(value):
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise SubscriptionError('Science 账号或组织标识无效，未执行切换。') from None


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def tree_stamp(root):
    """Detect independently edited inactive workspaces and concurrent writers."""
    if linked(root):
        raise SubscriptionError('会话目录不能是符号链接。')
    digest = hashlib.sha256()
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            path = Path(parent) / name
            meta = path.lstat()
            if stat.S_ISLNK(meta.st_mode):
                # Virtual environments normally contain interpreter symlinks.
                # Fingerprint the link itself; never inspect its destination.
                record = [str(path.relative_to(root)), 'l', os.readlink(path)]
                digest.update(json.dumps(record).encode())
                continue
            if linked(path) or not (stat.S_ISREG(meta.st_mode) or stat.S_ISDIR(meta.st_mode)):
                raise SubscriptionError('会话目录包含不支持的链接或特殊文件：' + str(path.relative_to(root)) + '。原数据已保留。')
            # Directories change mtime when populated by cp; file mtimes survive.
            record = [str(path.relative_to(root)), 'd' if path.is_dir() else 'f',
                      0 if path.is_dir() else meta.st_size, 0 if path.is_dir() else meta.st_mtime_ns]
            digest.update(json.dumps(record).encode())
    return digest.hexdigest()


def verify_database(path):
    wal = path.with_name(path.name + '-wal')
    if wal.exists() and wal.stat().st_size:
        raise SubscriptionError('会话库还有未合并的 WAL，未迁移。')
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise SubscriptionError('会话库检查失败，原数据已保留。')
        return {'frames': db.execute('SELECT count(*) FROM frames').fetchone()[0],
                'projects': db.execute('SELECT count(*) FROM projects').fetchone()[0]}


class NativeSwitcher:
    VERSION = '0.1.72'

    def __init__(self, manager):
        self.manager = manager
        self.store = manager.store
        layout = getattr(manager, 'layout', {}) if WINDOWS else {}
        self.native = Path(layout.get('auth', manager.home / '.claude-science'))
        self.data = Path(layout.get('data', manager.home / '.claude-science'))
        self.root = private_dir(self.store.root / 'native')
        self.slots = private_dir(self.root / 'accounts')
        self.journal = self.root / 'switch.json'
        # Publication and rollback use rename, so staging must share the data
        # volume even when Science stores its conversations on D: or E:.
        self.backups = self.data / '.csswitch-subscription-backups' if WINDOWS else self.root / 'backups'

    def json_file(self, path, default=None):
        return json.loads(private_read(path)) if path.exists() else default

    def active(self):
        return self.json_file(self.native / 'active-org.json', {})

    def key(self):
        keys = dict(line.split('=', 1) for line in private_read(self.native / 'encryption.key').decode().splitlines() if '=' in line)
        return keys['OAUTH_ENCRYPTION_KEY'].strip().encode()

    def read_token(self, path):
        data = private_read(path)
        key = self.key()
        if data.startswith(b'v2:'):
            raw = base64.b64decode(data[3:], validate=True)
            derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'',
                           info=b'operon:aes-256-gcm:oauth').derive(base64.b64decode(key, altchars=b'-_', validate=True))
            plain = AESGCM(derived).decrypt(raw[:12], raw[12:], b'v2:oauth')
        else:
            plain = Fernet(key).decrypt(data)
        return json.loads(plain)

    def token_path(self, account):
        return self.native / '.oauth-tokens' / (checked_uuid(account) + '.enc')

    def summary(self):
        active = self.active()
        state = self.store.snapshot()
        current = next((i for i, a in state['accounts'].items() if a['account_uuid'] == active.get('account_uuid')), None)
        journal = self.json_file(self.journal, {})
        authenticated = False
        try:
            if self.manager.science_state().get('running'):
                opener, origin = self.local_client()
                with opener.open(origin + '/api/auth/status', timeout=5) as response:
                    login = json.load(response)
                authenticated = bool(login.get('authenticated') and login.get('account_uuid') == active.get('account_uuid')
                                     and not any(login.get(k) for k in ('oauth_stale', 'reauth_required', 'restart_pending', 'rotation_breaker_tripped')))
        except Exception:
            pass
        return {'enabled': bool(state.get('native_mode')), 'account_id': current,
                'authenticated': authenticated,
                'initial_login_required': not bool(active.get('account_uuid')),
                'supported_versions': '0.1.71 / 0.1.72（Windows）' if WINDOWS else '0.1.72（macOS）',
                'backup_dir': str(self.backups),
                'last_switch': {k: journal[k] for k in ('phase', 'at', 'counts', 'history_transferred', 'history_shared') if k in journal}}

    def save_slot(self, active):
        account = checked_uuid(active['account_uuid'])
        token = self.read_token(self.token_path(account))
        if token.get('account_uuid') != account or token.get('org_uuid') != active['org_uuid']:
            raise SubscriptionError('Science 登录凭据与当前组织不一致，未切换。')
        slot = private_dir(self.slots / account)
        private_write(slot / 'token.enc', private_read(self.token_path(account)))
        private_write(slot / 'active.json', json.dumps(active).encode())
        journal = self.token_path(account).with_suffix('.refresh-journal')
        if journal.exists():
            private_write(slot / 'refresh-journal', private_read(journal))
        else:
            (slot / 'refresh-journal').unlink(missing_ok=True)
        return slot

    def remove(self, ident):
        """Forget an inactive account and its switch cache, never Science history."""
        if not isinstance(ident, str) or not ident:
            raise SubscriptionError('请选择要删除的账号。')
        pending = self.json_file(self.journal, {})
        if pending.get('phase') not in (None, 'complete', 'rolled_back'):
            raise SubscriptionError('账号切换尚未完成，请先恢复原账号，再删除。')
        with self.store.locked():
            state = self.store.read()
            account = state['accounts'].get(ident)
            if account is None:
                raise SubscriptionError('账号不存在或已删除，请刷新列表。')
            account_uuid = checked_uuid(account['account_uuid'])
            if self.active().get('account_uuid') == account_uuid:
                raise SubscriptionError('请先切换到其他账号，再删除当前 Science 账号。')
            slot = self.slots / account_uuid
            if linked(slot):
                raise SubscriptionError('账号登录缓存包含链接，未删除。')
            saved = {}
            if slot.exists():
                for path in slot.iterdir():
                    if path.name not in ('token.enc', 'active.json', 'refresh-journal') or not path.is_file() or linked(path):
                        raise SubscriptionError('账号登录缓存包含未知文件，未删除。')
                    saved[path.name] = private_read(path)
            had_slot = slot.exists()
            try:
                for name in saved:
                    (slot / name).unlink()
                if had_slot:
                    slot.rmdir()
                del state['accounts'][ident]
                if state.get('preferred') == ident:
                    state.pop('preferred', None)
                if state['selected'] == ident or not state['accounts']:
                    # Do not silently switch the legacy inference gateway to another identity.
                    state.update(selected='', mode='manual')
                state['revision'] += 1
                self.store.write(state)
            except Exception:
                if had_slot:
                    private_dir(slot)
                    for name, data in saved.items():
                        private_write(slot / name, data)
                raise
        return {'message': '账号已删除。本地项目、会话和历史备份已保留；再次使用需重新添加账号。'}

    def prepare_target(self, account):
        ident = checked_uuid(account['account_uuid'])
        slot = self.slots / ident
        if account.get('native_owned') and (slot / 'token.enc').exists():
            token = self.read_token(slot / 'token.enc')
        else:
            if not account.get('access_token') or not account.get('refresh_token'):
                raise SubscriptionError('此账号需要重新授权后才能用于 Science 登录。')
            # This is the real OAuth credential acquired by the user, transferred
            # once to Science. CSSwitch relinquishes it before Science starts.
            token = {'access_token': account['access_token'], 'refresh_token': account['refresh_token'],
                     'api_key': None, 'token_expires_at': datetime.fromtimestamp(account['expires_at'], timezone.utc).isoformat(),
                     'refresh_token_expires_at': None, 'provider': 'claude_ai', 'scopes': account['scopes'],
                     'email': account['email'], 'account_uuid': ident, 'org_uuid': account['org_uuid'],
                     'org_name': None, 'subscription_type': account.get('subscription_type'),
                     'billing_resolved': False}
            slot = private_dir(slot)
            private_write(slot / 'token.enc', Fernet(self.key()).encrypt(json.dumps(token).encode()))
            (slot / 'refresh-journal').unlink(missing_ok=True)
            private_write(slot / 'active.json', json.dumps({'account_uuid': ident, 'org_uuid': account['org_uuid'],
                                                          'login_owner_data_dir': str(self.data)}).encode())
        if token.get('account_uuid') != ident or token.get('org_uuid') != account['org_uuid']:
            raise SubscriptionError('目标账号的登录身份不匹配，未切换。')
        return slot

    def install_slot(self, account, slot):
        tokens = self.native / '.oauth-tokens'
        # The stopped native client requires exactly one .enc in its live slot.
        for path in tokens.glob('*.enc'):
            if linked(path):
                raise SubscriptionError('拒绝符号链接凭据。')
            path.unlink()
        private_write(self.token_path(account), private_read(slot / 'token.enc'))
        refresh = self.token_path(account).with_suffix('.refresh-journal')
        if (slot / 'refresh-journal').exists():
            private_write(refresh, private_read(slot / 'refresh-journal'))
        else:
            refresh.unlink(missing_ok=True)
        private_write(self.native / 'active-org.json', private_read(slot / 'active.json'))

    def clone(self, source, target):
        if target.exists():
            raise SubscriptionError('暂存目录已存在，未覆盖。')
        if WINDOWS:
            tree_stamp(source)
            shutil.copytree(source, target, copy_function=shutil.copy2, symlinks=True)
        else:
            self.manager.run('/bin/cp', ['-cRp', str(source), str(target)], timeout=300)

    def open_gui(self):
        if WINDOWS:
            self.manager.run(self.manager.science, ['open'])
        else:
            self.manager.run('/usr/bin/open', ['-a', '/Applications/Claude Science.app'])

    def launch(self, gateway=False, hot=False):
        if not WINDOWS and not gateway:
            # Let the native macOS shell establish its own local browser login.
            # Attaching it to a CLI-started daemon leaves its webview at the
            # single-use "Sign in" form even when OAuth is valid.
            if hot:
                self.manager.run('/usr/bin/open', ['-a', '/Applications/Claude Science.app',
                    '--env', 'ANTHROPIC_BASE_URL=' + self.manager.base_url(),
                    '--env', 'ANTHROPIC_API_KEY=', '--env', 'ANTHROPIC_AUTH_TOKEN='])
            else:
                self.open_gui()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                state = self.manager.science_state()
                if state.get('running'):
                    # The app publishes its daemon lock before the browser
                    # authentication routes finish initializing/rotating.
                    try:
                        opener, origin = self.local_client()
                        with opener.open(origin + '/api/auth/status', timeout=15) as response:
                            json.load(response)
                        return state
                    except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError, SubscriptionError):
                        pass
                time.sleep(.5)
            raise SubscriptionError('Science 应用尚未启动服务，请检查应用窗口。')
        env = {k: v for k, v in os.environ.items() if k not in (
            'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL',
            'ANTHROPIC_ORGANIZATION_ID', 'BUN_OPTIONS') and not k.startswith('OPERON_AUTH_')}
        if gateway:
            env['ANTHROPIC_BASE_URL'] = self.manager.base_url()
        self.manager.run(self.manager.science, ['serve', '--detached', '--no-browser'], timeout=180, env=env)
        state = self.manager.science_state()
        if not state.get('running'):
            raise SubscriptionError('Science 尚未启动，请检查本地服务。')
        return state

    def local_client(self):
        # Mint an official single-use local login URL; consume it in memory only.
        raw = self.manager.run(self.manager.science, ['url']).stdout
        urls = re.findall(r'http://[^\s\x1b]+', raw)
        port = self.manager.science_state().get('port')
        url = next((u for u in urls if urllib.parse.urlsplit(u).hostname in ('localhost', '127.0.0.1')
                    and urllib.parse.urlsplit(u).port == port), None)
        if not url:
            raise SubscriptionError('无法获取 Science 本机验证入口。')
        class LocalRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                parsed = urllib.parse.urlsplit(newurl)
                if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1') or parsed.port != port:
                    raise SubscriptionError('本机验证出现非本机跳转，已中止。')
                return super().redirect_request(req, fp, code, msg, headers, newurl)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), LocalRedirect(),
                                            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with opener.open(url, timeout=15) as response:
            response.read(1024)
        parsed = urllib.parse.urlsplit(url)
        nonce = urllib.parse.parse_qs(parsed.query).get('nonce', [None])[0]
        if not nonce:
            raise SubscriptionError('Science 本机登录链接缺少一次性凭据。')
        origin = f'http://{parsed.netloc}'
        request = urllib.request.Request(origin + '/api/auth/nonce',
            data=urllib.parse.urlencode({'nonce': nonce, 'dest': '/'}).encode(),
            headers={'Content-Type': 'application/x-www-form-urlencoded', 'Origin': origin})
        with opener.open(request, timeout=15) as response:
            response.read(1024)
        return opener, origin

    def verify_login(self, expected):
        opener, origin = self.local_client()
        token = self.read_token(self.token_path(expected['account_uuid']))
        expires = token.get('token_expires_at')
        if expires and datetime.fromisoformat(expires.replace('Z', '+00:00')).timestamp() <= time.time() + 30:
            # Science owns the live refresh token and its refresh journal/locks.
            # Ask its native handler to refresh rather than rotating a copy.
            with opener.open(urllib.request.Request(origin + '/api/csrf', headers={'Origin': origin}), timeout=15) as response:
                response.read()
            cookie = next((h.cookiejar for h in opener.handlers if isinstance(h, urllib.request.HTTPCookieProcessor)), [])
            csrf = next((c.value for c in cookie if c.name == 'operon_csrf'), '')
            req = urllib.request.Request(origin + '/api/auth/refresh-tier', data=b'{}',
                headers={'Origin': origin, 'Content-Type': 'application/json', 'x-operon-csrf': csrf})
            with opener.open(req, timeout=45) as response:
                response.read()
        with opener.open(origin + '/api/auth/status', timeout=15) as response:
            value = json.load(response)
        if not value.get('authenticated') or any(value.get(k) != expected.get(k) for k in ('account_uuid', 'org_uuid', 'email')) or any(value.get(k) for k in ('oauth_stale', 'reauth_required', 'restart_pending', 'rotation_breaker_tripped')):
            raise SubscriptionError('Science 实际登录身份未通过验证，已停止并尝试恢复原账号。')
        # authenticated:true only means a file exists. Confirm that the official
        # server accepts the current token and identifies the same real account.
        from subscription_usage import request_json
        token = self.read_token(self.token_path(expected['account_uuid']))
        try:
            profile = request_json('https://api.anthropic.com/api/oauth/profile', token=token['access_token'])
        except Exception:
            raise SubscriptionError('官方登录验证失败，未将本地身份文件当作登录成功；请重新授权或检查网络。') from None
        identity = profile.get('account') or profile
        organization = profile.get('organization') or {}
        if identity.get('uuid') != expected['account_uuid'] or (identity.get('email_address') or identity.get('email')) != expected['email'] or organization.get('uuid') != expected['org_uuid']:
            raise SubscriptionError('官方返回的账号或组织不匹配，未确认切换成功。')
        return True

    def close_gui(self):
        if WINDOWS:
            try:
                return close_science_windows(self.manager.home, self.manager.runner)
            except Exception:
                raise SubscriptionError('Science 窗口未退出，请关闭窗口后再切换。') from None
        app = Path('/Applications/Claude Science.app')
        if not (app / 'Contents/Info.plist').exists():
            return False
        with (app / 'Contents/Info.plist').open('rb') as stream:
            name = plistlib.load(stream)['CFBundleExecutable']
        if '/' in name or name in ('.', '..'):
            raise SubscriptionError('Science 应用入口无效。')
        executable = str(app / 'Contents/MacOS' / name)
        rows = self.manager.run('/bin/ps', ['-axo', 'pid=,args=']).stdout.splitlines()
        found = False
        for row in rows:
            parts = row.strip().split(None, 1)
            if len(parts) == 2 and parts[1] == executable:
                os.kill(int(parts[0]), signal.SIGTERM)
                found = True
                for _ in range(50):
                    try:
                        os.kill(int(parts[0]), 0)
                    except ProcessLookupError:
                        break
                    time.sleep(.1)
                else:
                    raise SubscriptionError('Science 窗口未退出，请关闭窗口后再切换。')
        return found

    def recover(self):
        operation = self.json_file(self.journal, {})
        if operation.get('phase') in (None, 'complete', 'rolled_back'):
            return {'message': '没有需要恢复的账号切换。'}
        backup = Path(operation['backup'])
        if backup.parent != self.backups or linked(backup):
            raise SubscriptionError('恢复目录无效，未改动数据。')
        active = self.active()
        if active.get('account_uuid') not in (operation['from'], operation['to']):
            raise SubscriptionError('Science 已登录其他账号，未自动覆盖，请先检查备份。')
        self.close_gui()
        if self.manager.science_state().get('running'):
            self.manager.run(self.manager.science, ['stop'], timeout=90)
        if self.manager.science_state().get('running'):
            raise SubscriptionError('Science 未停止，不能恢复。')
        # Save any rotations that occurred after the interrupted transaction.
        live_slot = self.save_slot(active)
        raw = private_read(backup / 'accounts.enc')
        framed = base64.b64decode(raw.removeprefix(b'CSSUB1:'), validate=True)
        original = json.loads(AESGCM(self.store.key()).decrypt(framed[:12], framed[12:], b'csswitch-subscriptions-v1'))
        old = self.json_file(backup / 'active.json')
        slot = live_slot if active['account_uuid'] == old['account_uuid'] else backup / 'original-login'
        self.install_slot(old['account_uuid'], slot)
        target_account = next(a for a in original['accounts'].values() if a['account_uuid'] == operation['to'])
        target = self.data / 'orgs' / checked_uuid(target_account['org_uuid'])
        if operation.get('shared_history'):
            SharedHistory(self.data).rollback(operation['shared_history'], backup)
        # Legacy journals remain recoverable after upgrading.
        # A published workspace has no history-next left in staging.
        if not (backup / 'history-next').exists() and (backup / 'history-source').exists() and target.exists():
            target.rename(backup / ('history-recovered-' + uuid.uuid4().hex[:8]))
        if (backup / 'history-target-before').exists() and not target.exists():
            (backup / 'history-target-before').rename(target)
        with self.store.locked():
            latest = self.store.read()
            # Keep newly authorized accounts; restore only routing and ownership.
            for i, a in latest['accounts'].items():
                if (self.slots / a['account_uuid'] / 'token.enc').exists():
                    a['native_owned'] = True
                    a.pop('access_token', None); a.pop('refresh_token', None)
            original_id = next((i for i, a in latest['accounts'].items() if a['account_uuid'] == old['account_uuid']), None)
            if not original_id:
                raise SubscriptionError('原 Science 账号不在账号列表，备份仍保留。')
            hot = bool(original.get('hot_mode'))
            latest.update(native_mode=True, hot_mode=hot, mode=original.get('mode', 'manual') if hot else 'manual', selected=original.get('selected', original_id) if hot else original_id, revision=latest['revision'] + 1)
            if hot:
                latest['preferred'] = original.get('preferred', original_id)
            self.store.write(latest)
        if hot:
            self.manager.ensure_gateway(allow_native=True)
            launched = self.launch(hot=True)
            private_write(self.store.root / 'science.json', json.dumps(launched).encode())
            if not self.manager.owned(launched):
                raise SubscriptionError('已恢复登录数据，订阅路由尚未接入，请重新连接。')
        else:
            self.launch()
        self.verify_login(latest['accounts'][original_id])
        operation['phase'] = 'rolled_back'
        private_write(self.journal, json.dumps(operation).encode())
        self.open_gui()
        return {'message': '已恢复原 Science 登录与会话工作区。已有恢复数据均已保留。'}

    def ensure_login_switch_idle(self):
        health = self.manager.health()
        if health and health.get('active_requests'):
            raise SubscriptionError('当前回复尚未结束，请完成后再同步 Science 登录。优先消耗设置仍可免重启更改。')
        active = self.active()
        if not active.get('org_uuid'):
            return
        path = SharedHistory(self.data).resolve(checked_uuid(active['org_uuid'])) / 'operon-cli.db'
        if path.is_file():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
                busy = db.execute("SELECT 1 FROM frames WHERE status IN ('processing','running','pending','queued') LIMIT 1").fetchone()
            if busy:
                raise SubscriptionError('Science 仍有任务运行，请任务完成或暂停后再同步登录。未中断任务，也未修改登录。')

    def switch(self, ident, preserve_hot=False):
        state = self.store.snapshot()
        hot = bool(state.get('hot_mode') and preserve_hot)
        if state.get('hot_mode') and not preserve_hot:
            raise SubscriptionError('免重启模式请使用「同步 Science 登录」切换真实邮箱。')
        if preserve_hot and not hot:
            raise SubscriptionError('请在免重启模式中同步登录，或使用原生切换。')
        if ident not in state['accounts']:
            raise SubscriptionError('请选择已授权的账号。')
        pending = self.json_file(self.journal, {})
        if pending.get('phase') not in (None, 'complete', 'rolled_back'):
            raise SubscriptionError('上次切换未完成，请先恢复；备份目录请查看订阅页，未覆盖现有数据。')
        account = state['accounts'][ident]
        current = self.active()
        if not current.get('account_uuid'):
            raise SubscriptionError('请先在官方 Science 完成一次登录并打开原会话，再使用订阅切换。现有对话未修改。')
        checked_uuid(current.get('account_uuid')); checked_uuid(current.get('org_uuid')); checked_uuid(account.get('org_uuid'))
        if current['account_uuid'] == account['account_uuid'] and state.get('native_mode') and account.get('native_owned'):
            if not self.manager.science_state().get('running'):
                if hot:
                    self.manager.ensure_gateway(allow_native=True)
                    launched = self.launch(hot=True)
                    private_write(self.store.root / 'science.json', json.dumps(launched).encode())
                    if not self.manager.owned(launched):
                        raise SubscriptionError('Science 尚未接入订阅服务，请恢复连接。')
                else:
                    self.launch()
            self.verify_login(account)
            self.open_gui()
            return {'message': 'Science 已登录此账号。打开原会话后可继续或 Resume。'}
        if hot:
            self.ensure_login_switch_idle()
        version = self.manager.run(self.manager.science, ['--version']).stdout
        versions = ('0.1.71', '0.1.72') if WINDOWS else (self.VERSION,)
        if not any(re.search(r'\b' + re.escape(v) + r'\b', version) for v in versions):
            raise SubscriptionError('当前 Science 版本尚未验证原生切换，请先适配；未修改登录。')
        config = self.native / 'config.toml'
        if config.exists() and not WINDOWS:
            import tomllib
            cfg = tomllib.loads(config.read_text())
            if cfg.get('data_dir_from') or Path(cfg.get('data_dir', str(self.native))).expanduser() != self.native or Path(cfg.get('paths', {}).get('auth_dir', str(self.native))).expanduser() != self.native:
                raise SubscriptionError('自定义 Science 数据或认证目录尚不支持原生切换。')
        shared = SharedHistory(self.data)
        shared.validate()
        source = shared.resolve(current['org_uuid'])
        target = shared.resolve(account['org_uuid'])
        if any(linked(p) for p in (self.native, self.data, *self.data.parents)) or not (source / 'operon-cli.db').is_file() or linked(source / 'operon-cli.db'):
            raise SubscriptionError('Science 原会话目录无效，未修改登录。')
        if linked(self.native / '.oauth-tokens'):
            raise SubscriptionError('拒绝符号链接凭据目录。')
        for path in (source, target):
            for name in ('operon-cli.db', 'operon-cli.db-wal', 'operon-cli.db-shm'):
                if linked(path / name):
                    raise SubscriptionError('会话数据库或 WAL 不能是链接，未修改登录。')
        # An enrolled account already uses the very same files. A new account
        # only merges once when it actually has an independent workspace.
        transfer_history = source != target
        target_stamp = tree_stamp(target) if transfer_history and target.exists() else None
        before = self.manager.science_state()
        was_gateway = self.manager.owned(before)
        # Validate the installed client's real credential schema before stopping
        # it or changing any history. An unknown encryption layout fails closed.
        self.read_token(self.token_path(current['account_uuid']))
        backup = private_dir(private_dir(self.backups) / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]))
        private_write(backup / 'accounts.enc', private_read(self.store.root / 'accounts.enc'))
        private_write(backup / 'active.json', private_read(self.native / 'active-org.json'))
        operation = {'phase': 'stopping', 'at': time.time(), 'backup': str(backup), 'from': current['account_uuid'], 'to': account['account_uuid'], 'history_transferred': transfer_history, 'history_shared': True}
        def phase(value):
            operation['phase'] = value
            private_write(self.journal, json.dumps(operation).encode())
        phase('stopping')
        gui = False
        old_slot = None
        installed = False
        sharing_plan = None
        attempted_start = False
        try:
            if hot:
                self.ensure_login_switch_idle()
            gui = self.close_gui()
            if before.get('running'):
                self.manager.run(self.manager.science, ['stop'], timeout=90)
            if self.manager.science_state().get('running'):
                raise SubscriptionError('Science 未停止，未修改登录与会话。')
            health = self.manager.health()
            deadline = time.monotonic() + 30
            while health and health.get('active_requests') and time.monotonic() < deadline:
                time.sleep(.2)
                health = self.manager.health()
            if health:
                if health.get('active_requests'):
                    raise SubscriptionError('网关仍有请求，请稍后切换。')
                if WINDOWS:
                    self.manager.gateway_request('/shutdown', post=True)
                else:
                    cmd = self.manager.run('/bin/ps', ['-p', str(health['pid']), '-o', 'command=']).stdout
                    if 'subscription_gateway.py' not in cmd:
                        raise SubscriptionError('无法确认订阅网关进程，未停止其他服务。')
                    os.kill(health['pid'], signal.SIGTERM)
                for _ in range(50):
                    if not self.manager.health():
                        break
                    time.sleep(.1)
                if self.manager.health():
                    raise SubscriptionError('网关未停止，未转交登录凭据。')
            old_slot = self.save_slot(current)
            # Retain immutable per-operation copies in addition to mutable slots.
            self.clone(old_slot, backup / 'original-login')
            old_slot = backup / 'original-login'
            target_slot = self.prepare_target(account)
            phase('preparing_history')
            if transfer_history and (tree_stamp(target) if target.exists() else None) != target_stamp:
                raise SubscriptionError('目标会话在切换时发生变化，未覆盖。')
            sharing_plan, operation['counts'] = shared.prepare(
                current['org_uuid'], account['org_uuid'], backup, self.clone)
            operation['shared_history'] = sharing_plan
            # Recovery must know every intended rename/link before publication.
            phase('prepared')
            shared.publish(sharing_plan, backup)
            installed = True
            self.install_slot(account['account_uuid'], target_slot)
            with self.store.locked():
                updated = self.store.read()
                if updated['revision'] != state['revision']:
                    raise SubscriptionError('账号配置在切换过程中改变，已中止。')
                updated.update(native_mode=True, hot_mode=hot, mode=state['mode'] if hot else 'manual', selected=ident, revision=updated['revision'] + 1)
                if hot:
                    updated['preferred'] = ident
                for a in updated['accounts'].values():
                    if a['account_uuid'] in (current['account_uuid'], account['account_uuid']):
                        a['native_owned'] = True
                        for key in ('access_token', 'refresh_token'):
                            a.pop(key, None)
                self.store.write(updated)
            phase('starting')
            attempted_start = True
            if hot:
                self.manager.ensure_gateway(allow_native=True)
                launched = self.launch(hot=True)
                private_write(self.store.root / 'science.json', json.dumps(launched).encode())
                if not self.manager.owned(launched):
                    raise SubscriptionError('真实登录已启动但未接入订阅服务，正在恢复原状态。')
            else:
                self.launch()
            self.verify_login(account)
            phase('complete')
            if not hot:
                (self.store.root / 'science.json').unlink(missing_ok=True)
            self.open_gui()
            return {'message': ('已同步 Science 真实登录邮箱并验证成功，免重启推理切换已重新接入。项目和会话已保留。' if hot else '已切换 Science 真实登录账号并验证成功。原项目与会话已保留；中断会话请点击 Resume 继续。' if transfer_history else
                                '已更新并验证 Science 官方登录，原项目与会话保持不变。'),
                    'native_login_verified': True, 'history_transferred': transfer_history, 'history_shared': True, 'counts': operation.get('counts')}
        except Exception as exc:
            # Never roll back an account's rotated token to a stale pre-launch copy.
            # Preserve the target's latest native token before restoring old login.
            operation['failed_phase'] = operation['phase']
            operation['error_type'] = type(exc).__name__
            if isinstance(exc, urllib.error.HTTPError):
                operation['http_status'] = exc.code
            try:
                if self.manager.science_state().get('running') and installed:
                    self.manager.run(self.manager.science, ['stop'], timeout=90)
                if installed:
                    if self.manager.science_state().get('running'):
                        raise SubscriptionError('Science 尚未停止，备份已保留，请关闭后恢复。')
                    if attempted_start:
                        self.save_slot(self.active())
                    self.install_slot(current['account_uuid'], old_slot)
                if sharing_plan:
                    shared.rollback(sharing_plan, backup)
                # Retain ownership if target reached native startup/refresh.
                with self.store.locked():
                    restored = state.copy()
                    restored['revision'] = self.store.read()['revision'] + 1
                    if attempted_start:
                        for a in restored['accounts'].values():
                            if a['account_uuid'] == account['account_uuid']:
                                a['native_owned'] = True
                                a.pop('access_token', None); a.pop('refresh_token', None)
                        if not restored.get('native_mode') and restored['accounts'][restored['selected']].get('native_owned'):
                            original_id = next((i for i, a in restored['accounts'].items() if a['account_uuid'] == current['account_uuid']), None)
                            if original_id:
                                restored.update(selected=original_id, mode='manual')
                    self.store.write(restored)
                if before.get('running') and not self.manager.science_state().get('running'):
                    if hot:
                        self.manager.ensure_gateway(allow_native=True)
                        launched = self.launch(hot=True)
                    else:
                        if was_gateway:
                            self.manager.ensure_gateway()
                        launched = self.launch(gateway=was_gateway)
                    if was_gateway or hot:
                        private_write(self.store.root / 'science.json', json.dumps(launched).encode())
                phase('rolled_back')
                if gui:
                    self.open_gui()
            except Exception:
                phase('recovery_required')
                raise SubscriptionError('切换未完成，自动恢复也未完成。已保留原登录与会话备份，请先恢复再操作。') from None
            if isinstance(exc, SubscriptionError):
                raise
            raise SubscriptionError('切换未完成，已恢复原账号；会话备份已保留。') from None
