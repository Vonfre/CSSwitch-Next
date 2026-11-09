import importlib.util
import io
import json
import os
import signal
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('subscription_manager', SCRIPTS / 'science-subscriptions.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from subscription_store import Store, SubscriptionError, private_read, private_write
from subscription_platform import WINDOWS, private_handle, linked


def account(name):
    return {'account_uuid': name, 'email': name + '@example.test', 'access_token': 'synthetic-' + name,
            'expires_at': time.time() + 3600, 'refresh_token': 'refresh-' + name, 'subscription_type': 'pro'}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name).resolve() / 'subscriptions')

    def test_status_does_not_take_manager_mutation_lock(self):
        manager = Mock()
        manager.manage.return_value = {'connected': True}
        output = io.StringIO()
        with patch.object(module, 'Manager', return_value=manager), patch.object(module.sys, 'argv', ['science-subscriptions.py', 'status']), patch.object(module.sys, 'stdout', output), patch.object(module, 'lock_file', side_effect=BlockingIOError):
            self.assertEqual(module.main(), 0)
        self.assertTrue(json.loads(output.getvalue())['ok'])
        manager.manage.assert_called_once_with('status', {'id': None})

    def test_non_directory_component_can_be_checked_before_its_parent(self):
        path = self.store.root / 'file'
        path.touch()
        self.assertFalse(linked(path / 'child'))

    def test_encrypted_private_public_whitelist(self):
        ident = self.store.add(account('A'))
        raw = (self.store.root / 'accounts.enc').read_bytes()
        self.assertTrue(raw.startswith(b'CSSUB1:'))
        for text in (b'synthetic-A', b'refresh-A', b'A@example.test'):
            self.assertNotIn(text, raw)
        public = json.dumps(self.store.public())
        self.assertNotIn('synthetic-', public)
        self.assertNotIn('refresh-', public)
        self.assertEqual(self.store.snapshot()['accounts'][ident]['email'], 'A@example.test')
        for p in (self.store.root / 'accounts.enc', self.store.root / 'vault.key'):
            if WINDOWS:
                with p.open('rb') as stream:
                    self.assertTrue(private_handle(stream.fileno()))
            else:
                self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        if WINDOWS:
            self.assertTrue((self.store.root / 'vault.key').read_bytes().startswith(b'CSDP1:'))
        else:
            self.assertEqual(self.store.root.stat().st_mode & 0o777, 0o700)

    def test_permission_and_symlink_refused(self):
        self.store.add(account('A'))
        key = self.store.root / 'vault.key'
        if WINDOWS:
            subprocess.run(['icacls.exe', str(key), '/grant', '*S-1-1-0:(R)'], check=True, capture_output=True)
        else:
            os.chmod(key, 0o644)
        with self.assertRaises(SubscriptionError):
            self.store.snapshot()
        link = self.store.root / 'link'
        try:
            link.symlink_to(key)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symlink privilege is unavailable; ACL rejection passed')
            raise
        with self.assertRaises(SubscriptionError):
            private_write(link, b'never')
        with self.assertRaises(SubscriptionError):
            Store(link / 'child')

    def test_tampered_ciphertext_is_not_overwritten(self):
        self.store.add(account('A'))
        path = self.store.root / 'accounts.enc'
        raw = path.read_bytes()[:-8] + b'AAAAAAAA'
        private_write(path, raw)
        with self.assertRaises(SubscriptionError):
            self.store.add(account('B'))
        self.assertEqual(path.read_bytes(), raw)

    def test_route_checks_and_latest_manual_selection_wins(self):
        a = self.store.add(account('A'))
        with self.assertRaises(SubscriptionError):
            self.store.route()
        b = self.store.add(account('B'))
        old = self.store.snapshot()['revision']
        self.store.route(b)
        self.store.note(a, old, 200)
        self.assertEqual(self.store.snapshot()['selected'], b)
        self.store.route()
        self.assertEqual(self.store.public()['mode'], 'auto')
        self.store.note(a, old, 401)
        with self.assertRaises(SubscriptionError):
            self.store.route(a)

    def test_old_request_cannot_invalidate_new_login(self):
        a = self.store.add(account('A'))
        self.store.add({**account('A'), 'access_token': 'new-credential'})
        self.store.note(a, 1, 401, access_token='synthetic-A')
        self.assertFalse(self.store.public()['accounts'][0]['lapsed'])

    def test_expired_unrefreshable_account_not_ready(self):
        a = self.store.add({**account('A'), 'expires_at': 1, 'refresh_token': None})
        self.assertEqual(self.store.public()['ready_accounts'], 0)
        with self.assertRaises(SubscriptionError):
            self.store.route(a)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        binary = self.home / ('.claude-science/bin/claude-science.exe' if WINDOWS else '.claude-science/bin/claude-science')
        binary.parent.mkdir(parents=True)
        binary.touch()
        self.calls = []
        self.state = {'running': True, 'pid': 12345, 'port': 8000, 'started_at': 'test-start'}
        self.launch_env = {}
        def runner(args, **kwargs):
            self.calls.append(args)
            if args[1] == 'status':
                out = json.dumps(self.state)
            elif args[1] == 'serve':
                self.launch_env = kwargs['env']
                self.state = {'running': True, 'pid': 23456, 'port': 8000, 'started_at': 'new-start'}
                out = ''
            elif args[0] == '/bin/ps':
                out = 'ANTHROPIC_BASE_URL=' + self.launch_env.get('ANTHROPIC_BASE_URL', '')
            else:
                out = ''
            return subprocess.CompletedProcess(args, 0, out, '')
        self.manager = module.Manager(self.home, runner, env={})
        self.manager.ensure_gateway = Mock()
        self.manager.health = Mock(return_value={'active_requests': 0})
        self.a = self.manager.store.add(account('A'))
        self.b = self.manager.store.add(account('B'))

    def test_routes_never_start_or_stop_science(self):
        self.assertTrue(self.manager.manage('select', {'id': self.b})['same_process'])
        self.assertTrue(self.manager.manage('auto')['same_process'])
        self.assertTrue(all(args[1] == 'status' for args in self.calls))
        self.assertEqual(self.manager.store.public()['mode'], 'auto')

    def test_priority_switch_off_keeps_fallback_and_does_not_clear_a_newer_choice(self):
        self.manager.manage('priority', {'id': self.a, 'enabled': True})
        self.manager.manage('priority', {'id': self.b, 'enabled': True})
        self.manager.manage('priority', {'id': self.a, 'enabled': False})
        self.assertEqual(self.manager.store.public()['preferred_id'], self.b)
        result = self.manager.manage('priority', {'id': self.b, 'enabled': False})
        public = self.manager.store.public()
        self.assertIsNone(public['preferred_id'])
        self.assertEqual(public['mode'], 'auto')
        self.assertEqual(public['next_account_id'], self.b)
        self.assertTrue(result['same_process'])
        self.assertEqual(self.manager.store.candidate_ids(self.manager.store.snapshot()), [self.b, self.a])
        self.assertTrue(all(args[1] == 'status' for args in self.calls))

    def test_delete_dispatch_removes_only_requested_account_without_service_changes(self):
        ident = self.manager.store.add({**account('removed'), 'account_uuid': '10000000-0000-4000-8000-000000000003'})
        result = self.manager.manage('delete', {'id': ident})
        self.assertIn('账号已删除', result['message'])
        self.assertEqual({a['id'] for a in self.manager.store.public()['accounts']}, {self.a, self.b})
        self.assertEqual(self.calls, [])

    def test_only_current_revision_inference_confirms_routing(self):
        revision = self.manager.store.snapshot()['revision']
        last = {'account_id': self.a, 'revision': revision, 'started_at': 1, 'completed_at': 2, 'secret': 'never-expose'}
        self.manager.health.return_value = {'last_inference': last}
        self.assertEqual(self.manager.status()['live_validation'], 'confirmed')
        self.assertNotIn('secret', self.manager.status()['last_inference'])
        self.manager.route(self.b)
        self.assertEqual(self.manager.status()['live_validation'], 'pending')
        # Even switching back does not turn an old completion into new evidence.
        self.manager.route(self.a)
        self.assertEqual(self.manager.status()['live_validation'], 'pending')
        last['revision'] = self.manager.store.snapshot()['revision']
        self.assertEqual(self.manager.status()['live_validation'], 'confirmed')
        self.manager.health.return_value = None
        self.assertEqual(self.manager.status()['live_validation'], 'pending')
        self.assertIsNone(self.manager.status()['last_inference'])

    @unittest.skipIf(WINDOWS, 'Legacy inference launcher is macOS-only; native switching tested separately')
    def test_running_unowned_science_remains_untouched(self):
        with self.assertRaisesRegex(SubscriptionError, '正在运行'):
            self.manager.connect()
        self.assertEqual(self.state['pid'], 12345)
        self.manager.ensure_gateway.assert_not_called()
        self.assertTrue(all(args[1] == 'status' for args in self.calls))

    @unittest.skipIf(WINDOWS, 'Legacy inference launcher is macOS-only')
    def test_start_keeps_auth_data_and_verifies_endpoint(self):
        self.state = {'running': False}
        self.manager.connect()
        self.assertEqual(self.launch_env['ANTHROPIC_BASE_URL'], self.manager.base_url())
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', self.launch_env)
        self.assertTrue(self.manager.status()['connected'])
        serve = [c for c in self.calls if c[1] == 'serve']
        self.assertEqual(serve[0][1:], ['serve', '--detached', '--no-browser'])
        self.manager.connect()
        self.assertEqual(len([c for c in self.calls if c[1] == 'serve']), 1)
        self.assertFalse(any('stop' in c for c in self.calls))

    @unittest.skipIf(WINDOWS, 'Windows never claims legacy gateway ownership')
    def test_reused_pid_not_connected(self):
        self.state = {'running': False}
        self.manager.connect()
        self.state['started_at'] = 'different-daemon'
        self.assertFalse(self.manager.status()['connected'])

    @unittest.skipIf(WINDOWS, 'Legacy inference launcher is macOS-only')
    def test_second_status_running_blocks_launch_race(self):
        self.manager.science_state = Mock(side_effect=[{'running': False}, self.state])
        with self.assertRaisesRegex(SubscriptionError, '其他程序'):
            self.manager.connect()
        self.assertFalse(any('serve' in c for c in self.calls))

    def test_login_opens_only_official_auth_url(self):
        self.manager.gateway_request = Mock(return_value={'url': 'https://claude.ai/oauth/authorize?state=test'})
        if WINDOWS:
            with patch.object(os, 'startfile') as opened:
                self.manager.manage('login')
                opened.assert_called_once_with('https://claude.ai/oauth/authorize?state=test')
        else:
            self.manager.manage('login')
            self.assertEqual(self.calls[-1][0], '/usr/bin/open')
        self.manager.gateway_request.return_value = {'url': 'https://example.test/steal'}
        with self.assertRaises(SubscriptionError):
            self.manager.manage('login')

    def test_subprocess_errors_do_not_return_credentials(self):
        self.manager.runner = Mock(return_value=subprocess.CompletedProcess([], 1, 'sensitive-token', 'secret'))
        with self.assertRaises(SubscriptionError) as caught:
            self.manager.science_state()
        self.assertNotIn('secret', str(caught.exception))
        self.assertNotIn('sensitive-token', str(caught.exception))


class GatewayLifecycleTests(unittest.TestCase):
    def test_detached_gateway_restarts_immediately_after_health_connections(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = module.Manager(Path(folder).resolve(), env={})
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            settings = manager.store.gateway()
            private_write(manager.store.root / 'gateway.json', json.dumps({**settings, 'port': port}).encode())
            pids = set()
            try:
                manager.ensure_gateway()
                first = manager.health()['pid']
                pids.add(first)
                manager.ensure_gateway()
                self.assertEqual(manager.health()['pid'], first)
                manager.gateway_request('/shutdown', post=True)
                for _ in range(60):
                    if not manager.health(): break
                    time.sleep(.1)
                pids.remove(first)
                manager.ensure_gateway()
                second = manager.health()['pid']
                pids.add(second)
                self.assertNotEqual(first, second)
                self.assertEqual(manager.health()['version'], '0.8.30')
            finally:
                for pid in pids:
                    try:
                        if manager.health() and manager.health()['pid'] == pid:
                            manager.gateway_request('/shutdown', post=True)
                            for _ in range(60):
                                if not manager.health(): break
                                time.sleep(.1)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main()
