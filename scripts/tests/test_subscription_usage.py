"""Quota tests use synthetic OAuth credentials and never contact official APIs."""
import io
import json
import sys
from pathlib import Path
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_science_native_switch as native_tests
from subscription_store import private_write
from subscription_usage import UsageService, UsageError, normalize, USAGE_URL, TOKEN_URL, try_lock

PAYLOAD = {'five_hour': {'utilization': 32.5, 'resets_at': '2026-11-10T03:00:00Z'},
           'seven_day': {'utilization': 80, 'resets_at': '2026-11-16T03:00:00Z'}}


class QuotaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = native_tests.NativeSwitchTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.requester = Mock(return_value=PAYLOAD)
        self.service = UsageService(self.fixture.manager, requester=self.requester)

    def test_normalize_unknown_is_not_full_and_values_are_validated(self):
        result = normalize(PAYLOAD)
        self.assertEqual([w['remaining_percent'] for w in result], [67.5, 20])
        self.assertIsNone(normalize({'five_hour': None})[0]['remaining_percent'])
        for invalid in [-1, 101, float('nan'), True, '20']:
            with self.assertRaises(ValueError):
                normalize({'five_hour': {'utilization': invalid}})
        with self.assertRaises(ValueError): normalize({})

    def test_scoped_model_limits_override_legacy_without_replacing_global_week(self):
        row = {'kind': 'weekly_scoped', 'group': 'weekly', 'percent': 15,
               'scope': {'model': {'display_name': 'Fable'}, 'surface': None}, 'resets_at': None}
        payload = {**PAYLOAD, 'seven_day_fable': {'utilization': 90},
                   'limits': [row, {**row, 'percent': 95}, {**row, 'scope': {'surface': 'code', 'model': {'display_name': 'Opus'}}}]}
        windows = {w['key']: w for w in normalize(payload)}
        self.assertEqual(windows['seven_day']['remaining_percent'], 20)
        self.assertEqual(windows['seven_day_fable']['remaining_percent'], 85)
        self.assertNotIn('seven_day_opus', windows)

    def test_new_authorization_invalidates_error_cache_immediately(self):
        self.service.refresh()
        with self.fixture.store.locked():
            state = self.fixture.store.read()
            state['revision'] += 1
            self.fixture.store.write(state)
        self.service.refresh()
        self.assertEqual(self.requester.call_count, 4)

    def test_individual_refresh_preserves_other_cache_and_rate_limit_across_selection(self):
        initial = self.service.refresh()['accounts']
        first, second = self.fixture.ids
        self.service.refresh(first, force=True)
        self.assertEqual(self.requester.call_count, 3)
        self.assertEqual(self.service.cache()[second]['fetched_at'], initial[second]['fetched_at'])
        cached = self.service.cache()
        cached[first].update(status='rate_limited', retry_at=time.time() + 180)
        private_write(self.service.path, json.dumps(cached).encode())
        with self.fixture.store.locked():
            state = self.fixture.store.read()
            state['revision'] += 1
            self.fixture.store.write(state)
        self.service.refresh(first, force=True)
        self.assertEqual(self.requester.call_count, 3)
        self.assertEqual(self.service.cache()[first]['status'], 'rate_limited')

    def test_live_credentials_win_even_before_ownership_transfer_and_cache_is_bounded(self):
        value = self.service.refresh()
        self.assertEqual(len(value['accounts']), 2)
        calls = self.requester.call_args_list
        self.assertEqual({c.kwargs['token'] for c in calls}, {'native-original', 'synthetic-1'})
        self.assertNotIn('native-original', json.dumps(value))
        self.assertNotIn('synthetic', self.service.path.read_text())
        self.service.refresh()
        self.assertEqual(self.requester.call_count, 2)
        self.assertTrue(all(v['status'] == 'ok' and not v['stale'] for v in value['accounts'].values()))

    def test_failures_retain_last_good_values_and_never_expose_response(self):
        previous = {'windows': normalize(PAYLOAD), 'fetched_at': time.time() - 120}
        for code, status in [(401, 'auth_required'), (403, 'forbidden'), (429, 'rate_limited'), (500, 'http_error')]:
            self.requester.side_effect = urllib.error.HTTPError(USAGE_URL, code, 'sensitive-token', {'Retry-After': '180'}, io.BytesIO(b'sensitive-token'))
            value = self.service.query(self.fixture.ids[0], self.fixture.store.snapshot()['accounts'][self.fixture.ids[0]], previous)
            self.assertEqual(value['status'], status)
            self.assertEqual(value['windows'], previous['windows'])
            self.assertNotIn('sensitive-token', json.dumps(value))
            if code == 429: self.assertGreaterEqual(value['retry_at'], value['checked_at'] + 180)

    def test_inactive_refresh_is_persisted_and_live_token_is_untouched(self):
        self.fixture.switcher.switch(self.fixture.ids[1])
        old_live = self.fixture.switcher.token_path(self.fixture.account_ids[1]).read_bytes()
        def request(url, token=None, form=None):
            if url == TOKEN_URL:
                self.assertEqual(form['refresh_token'], 'native-original-refresh')
                return {'access_token': 'new-access', 'refresh_token': 'new-refresh', 'expires_in': 3600}
            if token == 'native-original':
                raise urllib.error.HTTPError(url, 401, '', {}, io.BytesIO())
            self.assertEqual(token, 'new-access')
            return PAYLOAD
        self.service.requester = request
        account = self.fixture.store.snapshot()['accounts'][self.fixture.ids[0]]
        value = self.service.query(self.fixture.ids[0], account, {})
        self.assertEqual(value['status'], 'ok')
        slot = self.fixture.switcher.slots / self.fixture.account_ids[0] / 'token.enc'
        self.assertEqual(self.fixture.switcher.read_token(slot)['refresh_token'], 'new-refresh')
        self.assertEqual(self.fixture.switcher.token_path(self.fixture.account_ids[1]).read_bytes(), old_live)
        self.fixture.switcher.switch(self.fixture.ids[0])
        self.assertEqual(self.fixture.switcher.read_token(self.fixture.switcher.token_path(self.fixture.account_ids[0]))['access_token'], 'new-access')

    def test_refresh_does_not_compete_with_switch(self):
        ident = self.fixture.ids[1]
        account = self.fixture.store.snapshot()['accounts'][ident]
        with try_lock(self.fixture.store.root / '.manager.lock'):
            with self.assertRaises(UsageError) as error:
                self.service.refresh_inactive(ident, account)
        self.assertEqual(error.exception.status, 'busy')
        self.requester.assert_not_called()

    def test_deleted_account_is_removed_and_slow_queries_do_not_own_manager_lock(self):
        def request(*args, **kwargs):
            with try_lock(self.fixture.store.root / '.manager.lock'):
                pass
            return PAYLOAD
        # One account avoids two test workers competing for our assertion lock.
        with self.fixture.store.locked():
            state = self.fixture.store.read()
            del state['accounts'][self.fixture.ids[1]]
            self.fixture.store.write(state)
        self.service.requester = request
        result = self.service.refresh()
        self.assertEqual(list(result['accounts']), [self.fixture.ids[0]])
        self.assertEqual(result['accounts'][self.fixture.ids[0]]['status'], 'ok')

    def test_redirects_are_not_followed(self):
        from subscription_gateway import NoRedirect
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://untrusted.test'))


if __name__ == '__main__': unittest.main()
