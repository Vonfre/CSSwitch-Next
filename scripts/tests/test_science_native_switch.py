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
from unittest.mock import Mock

if sys.platform == 'win32':
    raise unittest.SkipTest('Native subscription storage uses POSIX locks and is not supported on Windows.')

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
        self.assertTrue(list((self.switcher.root / 'backups').glob('*/history-failed')))

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
        (self.source / 'outside').symlink_to(self.home)
        with self.assertRaisesRegex(SubscriptionError, '链接'):
            tree_stamp(self.source)


if __name__ == '__main__':
    unittest.main()
