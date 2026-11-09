"""Native handoff with real encrypted files/SQLite and a simulated daemon."""
import base64
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from science_native_switch import NativeSwitcher, tree_stamp, file_hash
from subscription_store import Store, SubscriptionError, private_dir, private_write


class NativeSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.native = private_dir(self.home / '.claude-science')
        self.store = Store(self.home / '.csswitch-next/subscriptions')
        self.account_ids = ['10000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000002']
        self.orgs = ['20000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000002']
        self.ids = [self.store.add({'account_uuid': a, 'org_uuid': o, 'email': f'{i}@example.test',
                                   'access_token': f'synthetic-{i}', 'refresh_token': f'refresh-{i}',
                                   'expires_at': time.time()+3600, 'scopes': 'user:inference user:profile'})
                    for i, (a, o) in enumerate(zip(self.account_ids, self.orgs))]
        key = Fernet.generate_key()
        private_write(self.native / 'encryption.key', b'OAUTH_ENCRYPTION_KEY=' + key + b'\n')
        self.old = {'account_uuid': self.account_ids[0], 'org_uuid': self.orgs[0]}
        private_write(self.native / 'active-org.json', json.dumps(self.old).encode())
        tokens = private_dir(self.native / '.oauth-tokens')
        private_write(tokens / (self.account_ids[0] + '.enc'), Fernet(key).encrypt(json.dumps({
            **self.old, 'email': '0@example.test', 'access_token': 'native-original', 'refresh_token': 'native-original-refresh'}).encode()))
        self.source = self.native / 'orgs' / self.orgs[0]
        self.source.mkdir(parents=True)
        with closing(sqlite3.connect(self.source / 'operon-cli.db')) as db, db:
            db.executescript("CREATE TABLE frames(id TEXT PRIMARY KEY, status TEXT); CREATE TABLE projects(id TEXT); CREATE TABLE frame_messages(frame_id TEXT, idx INTEGER, msg_json TEXT); INSERT INTO frames VALUES('resume-frame','interrupted'); INSERT INTO projects VALUES('project'); INSERT INTO frame_messages VALUES('resume-frame',0,'{\"tool_result\":37}');")
        (self.source / 'tool-results').mkdir()
        (self.source / 'tool-results/result.txt').write_text('37')
        self.running = True
        self.commands = []
        def run(exe, args, **kwargs):
            self.commands.append(args)
            if args == ['--version']:
                output = 'claude-science 0.1.72'
            elif args == ['stop']:
                self.running = False
                output = ''
            elif args[0] == '-cRp':
                shutil.copytree(args[1], args[2], copy_function=shutil.copy2)
                output = ''
            else:
                output = ''
            return subprocess.CompletedProcess([], 0, output, '')
        self.manager = SimpleNamespace(home=self.home, store=self.store, science=self.native / 'bin/claude-science', run=run,
            science_state=lambda: {'running': self.running, 'pid': 1 if self.running else None}, owned=lambda s: False,
            health=lambda: None, ensure_gateway=Mock(), base_url=lambda: 'http://127.0.0.1:1/synthetic')
        self.switcher = NativeSwitcher(self.manager)
        self.switcher.close_gui = Mock(return_value=False)
        self.switcher.verify_login = Mock(return_value=True)
        def launch(gateway=False):
            self.running = True
            return self.manager.science_state()
        self.switcher.launch = Mock(side_effect=launch)

    def test_real_credentials_move_to_one_owner_and_resume_state_survives_roundtrip(self):
        before = file_hash(self.source / 'operon-cli.db')
        result = self.switcher.switch(self.ids[1])
        self.assertTrue(result['native_login_verified'])
        target = self.native / 'orgs' / self.orgs[1]
        self.assertEqual(file_hash(target / 'operon-cli.db'), before)
        self.assertEqual(self.switcher.active()['account_uuid'], self.account_ids[1])
        self.assertEqual(len(list((self.native / '.oauth-tokens').glob('*.enc'))), 1)
        self.assertEqual(self.switcher.read_token(self.switcher.token_path(self.account_ids[1]))['access_token'], 'synthetic-1')
        state = self.store.snapshot()
        self.assertTrue(state['native_mode'])
        self.assertEqual(state['selected'], self.ids[1])
        for a in state['accounts'].values():
            self.assertNotIn('access_token', a)
            self.assertNotIn('refresh_token', a)
        with closing(sqlite3.connect(target / 'operon-cli.db')) as db, db:
            self.assertEqual(db.execute('SELECT status FROM frames').fetchone()[0], 'interrupted')
            db.execute("UPDATE frames SET status='completed'")
            db.execute("INSERT INTO frame_messages VALUES('resume-frame',1,'{\"result\":38}')")
        # Simulate a native refresh: the inactive slot must be refreshed on exit.
        token = self.switcher.read_token(self.switcher.token_path(self.account_ids[1]))
        token['refresh_token'] = 'rotated-native'
        private_write(self.switcher.token_path(self.account_ids[1]), Fernet(self.switcher.key()).encrypt(json.dumps(token).encode()))
        self.switcher.switch(self.ids[0])
        with closing(sqlite3.connect(self.source / 'operon-cli.db')) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM frame_messages').fetchone()[0], 2)
        self.assertEqual(self.switcher.read_token(self.switcher.slots / self.account_ids[1] / 'token.enc')['refresh_token'], 'rotated-native')
        self.assertEqual(self.switcher.read_token(self.switcher.token_path(self.account_ids[0]))['access_token'], 'native-original')

    def test_independent_target_history_is_not_overwritten(self):
        target = self.native / 'orgs' / self.orgs[1]
        target.mkdir()
        (target / 'work.txt').write_text('independent work')
        with self.assertRaisesRegex(SubscriptionError, '独立'):
            self.switcher.switch(self.ids[1])
        self.assertEqual((target / 'work.txt').read_text(), 'independent work')
        self.assertTrue(self.running)
        self.assertNotIn(['stop'], self.commands)

    def test_modified_inactive_history_blocks_return(self):
        self.switcher.switch(self.ids[1])
        (self.source / 'external.txt').write_text('changed independently')
        with self.assertRaisesRegex(SubscriptionError, '独立'):
            self.switcher.switch(self.ids[0])
        self.assertEqual(self.switcher.active()['account_uuid'], self.account_ids[1])

    def test_native_verification_failure_rolls_back_and_preserves_backup(self):
        self.switcher.verify_login.side_effect = SubscriptionError('身份不匹配')
        with self.assertRaisesRegex(SubscriptionError, '身份不匹配'):
            self.switcher.switch(self.ids[1])
        self.assertEqual(self.switcher.active()['account_uuid'], self.account_ids[0])
        self.assertTrue(self.running)
        self.assertEqual(self.switcher.summary()['last_switch']['phase'], 'rolled_back')
        self.assertEqual((self.source / 'tool-results/result.txt').read_text(), '37')
        self.assertFalse((self.native / 'orgs' / self.orgs[1]).exists())
        self.assertTrue(list(self.switcher.backups.glob('*/history-failed')))

    def test_failed_clone_keeps_original_login_and_history(self):
        self.switcher.clone = Mock(side_effect=OSError('simulated full disk'))
        with self.assertRaisesRegex(SubscriptionError, '已恢复'):
            self.switcher.switch(self.ids[1])
        self.assertEqual(self.switcher.active()['account_uuid'], self.account_ids[0])
        self.assertTrue(self.running)
        self.assertEqual(self.store.snapshot()['selected'], self.ids[0])

    def test_pending_journal_is_not_overwritten(self):
        private_write(self.switcher.journal, b'{"phase":"starting"}')
        with self.assertRaisesRegex(SubscriptionError, '上次切换'):
            self.switcher.switch(self.ids[1])
        self.assertTrue(self.running)

    def test_recovery_preserves_latest_target_token_and_restores_source(self):
        self.switcher.switch(self.ids[1])
        operation = json.loads(self.switcher.journal.read_text())
        operation['phase'] = 'starting'  # Crash just before completion publication.
        private_write(self.switcher.journal, json.dumps(operation).encode())
        result = self.switcher.recover()
        self.assertIn('已恢复', result['message'])
        self.assertEqual(self.switcher.active()['account_uuid'], self.account_ids[0])
        self.assertTrue(self.running)
        self.assertTrue(self.store.snapshot()['native_mode'])
        self.assertEqual(self.switcher.summary()['last_switch']['phase'], 'rolled_back')

    def test_missing_source_database_fails_before_stop(self):
        (self.source / 'operon-cli.db').unlink()
        with self.assertRaisesRegex(SubscriptionError, '原会话'):
            self.switcher.switch(self.ids[1])
        self.assertTrue(self.running)
        self.assertNotIn(['stop'], self.commands)

    def test_symlink_history_is_rejected(self):
        try:
            (self.source / 'outside').symlink_to(self.home, target_is_directory=True)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege is unavailable')
            raise
        with self.assertRaisesRegex(SubscriptionError, '链接'):
            tree_stamp(self.source)

    def test_missing_initial_login_preserves_history(self):
        (self.native / 'active-org.json').unlink()
        before = file_hash(self.source / 'operon-cli.db')
        with self.assertRaisesRegex(SubscriptionError, '先在官方 Science'):
            self.switcher.switch(self.ids[1])
        self.assertEqual(file_hash(self.source / 'operon-cli.db'), before)
        self.assertNotIn(['stop'], self.commands)

    @unittest.skipUnless(os.name == 'nt', 'Windows custom data volume')
    def test_custom_data_and_auth_directories_keep_staging_on_data_volume(self):
        data = self.home / '研究 数据'
        data.mkdir()
        shutil.move(str(self.native / 'orgs'), str(data / 'orgs'))
        self.manager.layout = {'data': str(data), 'auth': str(self.native)}
        switcher = NativeSwitcher(self.manager)
        switcher.close_gui = Mock(return_value=False)
        switcher.launch = self.switcher.launch
        switcher.verify_login = Mock(return_value=True)
        switcher.switch(self.ids[1])
        self.assertEqual(switcher.backups.parent, data)
        self.assertTrue((data / 'orgs' / self.orgs[1] / 'operon-cli.db').exists())
        self.assertEqual(switcher.active()['account_uuid'], self.account_ids[1])

    @unittest.skipUnless(os.name == 'nt', 'NTFS junctions')
    def test_junction_history_rejected_without_symlink_privilege(self):
        link = self.source / 'junction'
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(self.home)],
                       check=True, capture_output=True)
        with self.assertRaisesRegex(SubscriptionError, '链接'):
            tree_stamp(self.source)

    def test_unknown_native_token_layout_fails_before_stop(self):
        private_write(self.switcher.token_path(self.account_ids[0]), b'unsupported-encryption')
        with self.assertRaises(Exception):
            self.switcher.switch(self.ids[1])
        self.assertNotIn(['stop'], self.commands)
        self.assertFalse(self.switcher.journal.exists())

    def test_v2_native_credentials_preserve_identity_and_refresh_owner(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        path = self.switcher.token_path(self.account_ids[0])
        token = self.switcher.read_token(path)
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'',
                   info=b'operon:aes-256-gcm:oauth').derive(base64.urlsafe_b64decode(self.switcher.key()))
        nonce = os.urandom(12)
        private_write(path, b'v2:' + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, json.dumps(token).encode(), b'v2:oauth')))
        if os.name == 'nt':
            original = self.manager.run
            self.manager.run = lambda exe, args, **kw: subprocess.CompletedProcess([], 0, '0.1.71', '') if args == ['--version'] else original(exe, args, **kw)
        self.switcher.switch(self.ids[1])
        saved = self.switcher.read_token(self.switcher.slots / self.account_ids[0] / 'token.enc')
        self.assertEqual(saved, token)
        self.assertNotIn('refresh_token', self.store.snapshot()['accounts'][self.ids[0]])

    def test_v2_keys_accept_standard_and_urlsafe_base64(self):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.hkdf import HKDF
        from cryptography.hazmat.primitives import hashes
        # Force both alphabet-specific characters; random keys made this intermittent.
        raw_key = b'\xfb\xff' * 16
        key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b'',
                   info=b'operon:aes-256-gcm:oauth').derive(raw_key)
        token = {**self.old, 'refresh_token': 'synthetic-v2-refresh'}
        nonce = bytes(range(12))
        encrypted = b'v2:' + base64.b64encode(nonce + AESGCM(key).encrypt(
            nonce, json.dumps(token).encode(), b'v2:oauth'))
        path = self.switcher.token_path(self.account_ids[0])
        private_write(path, encrypted)
        for encode in (base64.b64encode, base64.urlsafe_b64encode):
            with self.subTest(encoding=encode.__name__):
                private_write(self.native / 'encryption.key', b'OAUTH_ENCRYPTION_KEY=' + encode(raw_key) + b'\n')
                self.assertEqual(self.switcher.read_token(path), token)
                self.assertEqual(path.read_bytes(), encrypted)

    def test_delete_inactive_native_account_preserves_live_login_history_and_backups(self):
        self.switcher.switch(self.ids[1])
        self.switcher.switch(self.ids[0])
        slot = self.switcher.slots / self.account_ids[1]
        private_write(slot / 'refresh-journal', b'synthetic-refresh-journal')
        history = tree_stamp(self.native / 'orgs')
        backups = tree_stamp(self.switcher.backups)
        live = file_hash(self.switcher.token_path(self.account_ids[0]))
        active = self.switcher.active()
        revision = self.store.snapshot()['revision']
        self.commands.clear()
        result = self.switcher.remove(self.ids[1])
        self.assertIn('账号已删除', result['message'])
        self.assertFalse(slot.exists())
        self.assertEqual(self.switcher.active(), active)
        self.assertEqual(file_hash(self.switcher.token_path(self.account_ids[0])), live)
        self.assertEqual(tree_stamp(self.native / 'orgs'), history)
        self.assertEqual(tree_stamp(self.switcher.backups), backups)
        self.assertEqual(self.commands, [])
        state = self.store.snapshot()
        self.assertNotIn(self.ids[1], state['accounts'])
        self.assertEqual(state['selected'], self.ids[0])
        self.assertEqual(state['revision'], revision + 1)
        self.store.note(self.ids[1], revision, 200)
        self.assertNotIn(self.ids[1], self.store.snapshot()['accounts'])

    def test_delete_rejects_current_account_even_when_stopped_and_pending_switch(self):
        before = (self.store.root / 'accounts.enc').read_bytes()
        for running in (True, False):
            self.running = running
            with self.assertRaisesRegex(SubscriptionError, '当前 Science 账号'):
                self.switcher.remove(self.ids[0])
        private_write(self.switcher.journal, json.dumps({'phase': 'recovery_required'}).encode())
        with self.assertRaisesRegex(SubscriptionError, '先恢复'):
            self.switcher.remove(self.ids[1])
        self.assertEqual((self.store.root / 'accounts.enc').read_bytes(), before)
        self.assertEqual(self.commands, [])

    def test_delete_write_failure_restores_cached_credentials(self):
        slot = self.switcher.prepare_target(self.store.snapshot()['accounts'][self.ids[1]])
        before = {p.name: p.read_bytes() for p in slot.iterdir()}
        encrypted = (self.store.root / 'accounts.enc').read_bytes()
        with patch.object(self.store, 'write', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                self.switcher.remove(self.ids[1])
        self.assertEqual({p.name: p.read_bytes() for p in slot.iterdir()}, before)
        self.assertEqual((self.store.root / 'accounts.enc').read_bytes(), encrypted)

    def test_delete_selected_legacy_and_last_unconfigured_account_clears_selection(self):
        self.store.route(self.ids[1])
        self.switcher.remove(self.ids[1])
        state = self.store.snapshot()
        self.assertEqual((state['selected'], state['mode']), ('', 'manual'))
        # A final saved account can be removed when Science has no configured identity.
        (self.native / 'active-org.json').unlink()
        history = tree_stamp(self.native / 'orgs')
        self.switcher.remove(self.ids[0])
        self.assertEqual(self.store.public()['accounts'], [])
        self.assertEqual(self.store.public()['ready_accounts'], 0)
        self.assertEqual(tree_stamp(self.native / 'orgs'), history)
        for ident in (None, [], '../native', self.ids[0]):
            with self.assertRaises(SubscriptionError):
                self.switcher.remove(ident)

    def test_delete_rejects_unknown_cache_files_without_partial_changes(self):
        slot = self.switcher.prepare_target(self.store.snapshot()['accounts'][self.ids[1]])
        private_write(slot / 'unexpected.txt', b'keep')
        before = {p.name: p.read_bytes() for p in slot.iterdir()}
        with self.assertRaisesRegex(SubscriptionError, '未知文件'):
            self.switcher.remove(self.ids[1])
        self.assertEqual({p.name: p.read_bytes() for p in slot.iterdir()}, before)
        self.assertIn(self.ids[1], self.store.snapshot()['accounts'])


if __name__ == '__main__':
    unittest.main()
