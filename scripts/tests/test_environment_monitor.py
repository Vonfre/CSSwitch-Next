"""Synthetic public-network fixtures only; never contact accounts or real endpoints."""
import io
import json
from pathlib import Path
import ssl
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import environment_monitor as env


def trace_result(ip='8.8.8.8', country='US'):
    return {'status': 200, 'body': f'ip={ip}\nloc={country}\ntls=TLSv1.3\n', 'latency_ms': 20}


def intel_result(ip='8.8.8.8', **overrides):
    return {'status': 200, 'body': json.dumps({'ip': ip, 'is_tor': False, 'is_abuser': False,
            'is_datacenter': False, 'is_proxy': False, 'is_vpn': False, 'is_crawler': False, **overrides})}


class EnvironmentMonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = time.time()
        self.local = {'fingerprint': 'network-a', 'proxy': False, 'pac': False, 'socks': False}
        self.network = Mock(side_effect=lambda: self.local.copy())
        self.requester = Mock(side_effect=lambda url: intel_result() if ('ipapi' in url or 'iprisk' in url) else trace_result() if url in env.TRACE_URLS.values() else {'status': 200, 'body': '{}'})
        self.monitor = env.Monitor(Path(self.tmp.name).resolve() / 'environment', requester=self.requester, network=self.network, clock=lambda: self.now)

    def test_startup_status_does_not_contact_network_and_ticks_are_cached(self):
        self.assertTrue(self.monitor.manage('status')['enabled'])
        self.requester.assert_not_called()
        first = self.monitor.manage('tick')
        self.assertEqual(first['observations']['api']['ip'], '8.8.8.8')
        self.assertEqual(self.requester.call_count, 6)
        self.now += 30
        self.monitor.manage('tick')
        self.assertEqual(self.requester.call_count, 6)
        self.now += 271
        self.monitor.manage('tick')
        self.assertEqual(self.requester.call_count, 11)  # IP intelligence cached for 24 hours.

    def test_manual_refresh_detects_new_exit_within_60_seconds_with_same_proxy_config(self):
        first = self.monitor.manage('refresh')
        self.now += 2
        self.requester.side_effect = lambda url: intel_result('1.1.1.1') if 'iprisk' in url else trace_result('1.1.1.1') if url in env.TRACE_URLS.values() else {'status': 200, 'body': '{}'}
        result = self.monitor.manage('refresh')
        self.assertEqual(self.requester.call_count, 12)
        self.assertEqual(result['observations']['api']['ip'], '1.1.1.1')
        self.assertEqual(result['intelligence']['ip'], '1.1.1.1')
        self.assertGreater(result['checked_at'], first['checked_at'])

    def test_manual_refresh_retries_immediately_after_transient_failure(self):
        good = self.requester.side_effect
        for failure in ({'error': 'network'}, {'status': 403, 'error': 'http'}):
            self.requester.side_effect = lambda url: failure
            self.monitor.manage('refresh')
            self.requester.side_effect = good
            self.now += 1
            result = self.monitor.manage('refresh')
            self.assertEqual(result['observations']['api']['ip'], '8.8.8.8')
            self.assertEqual(result['checked_at'], self.now)

    def test_rate_limited_service_does_not_block_fresh_exit_detection(self):
        good = self.requester.side_effect
        self.requester.side_effect = lambda url: {'status': 429, 'error': 'http', 'retry_seconds': 1800} if url == env.STATUS_URL else good(url)
        self.monitor.manage('refresh')
        self.requester.reset_mock()
        self.now += 1
        result = self.monitor.manage('refresh')
        self.assertEqual(self.requester.call_count, 4)
        self.assertNotIn(env.STATUS_URL, [c.args[0] for c in self.requester.call_args_list])
        self.assertEqual(result['observations']['api']['ip'], '8.8.8.8')
        self.assertEqual(result['checked_at'], self.now)

    def test_network_change_refreshes_early_but_respects_minimum_interval(self):
        self.monitor.manage('tick')
        self.local['fingerprint'] = 'network-b'
        self.now += 30
        self.monitor.manage('tick')
        self.assertEqual(self.requester.call_count, 6)
        self.now += 31
        self.monitor.manage('tick')
        self.assertEqual(self.requester.call_count, 11)

    def test_disabling_survives_restarts_and_manual_refresh_still_works(self):
        self.monitor.manage('configure', False)
        self.monitor.manage('tick')
        self.requester.assert_not_called()
        self.network.assert_not_called()
        self.assertFalse(env.Monitor(self.monitor.root).manage('status')['enabled'])
        self.assertFalse(self.monitor.manage('refresh')['enabled'])
        self.assertEqual(self.requester.call_count, 6)
        self.monitor.manage('clear')
        self.assertFalse(self.monitor.manage('status')['enabled'])

    def test_rate_limit_not_retried_by_manual_or_network_change_or_clear(self):
        self.requester.side_effect = lambda url: {'status': 429, 'error': 'http', 'retry_seconds': 1800}
        first = self.monitor.manage('tick')
        self.assertEqual(self.requester.call_count, 5)
        self.now += 301
        self.local['fingerprint'] = 'network-b'
        result = self.monitor.manage('refresh')
        self.assertEqual(result['checked_at'], first['checked_at'])
        self.assertGreater(result['refresh_deferred_until'], self.now)
        self.monitor.manage('clear')
        self.monitor.manage('refresh')
        self.monitor.manage('tick')
        self.assertEqual(self.requester.call_count, 5)

    def test_missing_partial_and_wrong_ip_intelligence_never_become_safe(self):
        for payload in ({}, {'is_tor': 'false'}, {'ip': '1.1.1.1', 'is_tor': False}):
            normalized = env.intelligence({'status': 200, 'body': json.dumps(payload)}, '8.8.8.8')
            self.assertNotEqual(normalized['status'], 'ok')
        observations = {k: env.trace(trace_result()) for k in env.TRACE_URLS}
        verdict = env.assess(observations, env.intelligence(intel_result(is_vpn=None), '8.8.8.8'), {}, self.now, self.local)
        self.assertEqual(verdict['level'], 'unknown')
        self.assertEqual(next(c for c in verdict['checks'] if c['id'] == 'is_vpn')['level'], 'unknown')
        self.assertEqual(env.trace({'status': 200, 'body': 'ip=127.0.0.1\n'})['error'], 'invalid_response')

    def test_403_and_outage_are_unknown_not_a_banned_account_and_old_ip_is_not_reused(self):
        self.monitor.manage('tick')
        self.now += 301
        self.requester.side_effect = lambda url: {'status': 403, 'error': 'http'}
        result = self.monitor.manage('tick')
        self.assertEqual(result['level'], 'unknown')
        self.assertNotIn('ip', result['observations']['api'])
        self.assertNotIn('ip', result['intelligence'])
        self.assertEqual(result['checks'][0]['level'], 'unknown')
        self.assertIn('不能据此判断账号被封', result['checks'][0]['detail'])

    def test_flags_region_changes_and_ipv6_are_classified_without_fake_scores(self):
        obs = {k: env.trace(trace_result()) for k in env.TRACE_URLS}
        base = env.assess(obs, env.intelligence(intel_result(is_datacenter=True, is_vpn=True), '8.8.8.8'), {}, self.now, self.local)
        self.assertFalse(any(c['level'] == 'warning' for c in base['checks']))
        high = env.assess(obs, env.intelligence(intel_result(is_tor=True), '8.8.8.8'), {}, self.now, self.local)
        self.assertEqual(high['level'], 'warning')
        obs['api'] = env.trace(trace_result('2001:4860:4860::8888', 'CN'))
        changed = env.assess(obs, {}, base, self.now + 301, self.local)
        self.assertEqual(len(changed['history']), 1)
        self.assertEqual(next(c for c in changed['checks'] if c['id'] == 'region')['level'], 'warning')
        self.assertEqual(next(c for c in changed['checks'] if c['id'] == 'changes')['level'], 'warning')
        self.assertNotIn('score', json.dumps(changed))

    def test_bounded_local_cache_private_permissions_clear_and_stale_results(self):
        self.monitor.manage('tick')
        value = self.monitor.read()
        value['history'] = [{'at': self.now - 86401, 'endpoint': 'api'}]
        self.monitor.write(value)
        self.assertEqual(self.monitor.manage('status')['history'], [])
        self.now += 370
        self.assertTrue(self.monitor.manage('status')['stale'])
        if sys.platform != 'win32':
            self.assertEqual((self.monitor.root / 'state.json').stat().st_mode & 0o777, 0o600)
        self.monitor.manage('clear')
        cleared = self.monitor.manage('status')
        self.assertNotIn('observations', cleared)
        self.assertEqual(cleared['history'], [])
        self.assertNotIn('network_fingerprint', cleared)

    def test_ip_intelligence_outage_backoff_applies_across_changing_ips(self):
        ip = ['8.8.8.8']
        self.requester.side_effect = lambda url: {'error': 'network'} if 'ipapi' in url else trace_result(ip[0])
        self.monitor.manage('tick')
        self.now += 301
        ip[0] = '1.1.1.1'
        result = self.monitor.manage('tick')
        self.assertEqual(sum('ipapi' in c.args[0] for c in self.requester.call_args_list), 1)
        self.assertEqual(result['intelligence']['status'], 'unknown')

    def test_source_scores_are_validated_and_never_replace_risk_evidence(self):
        value = env.intelligence(intel_result(trust_score=99, asn=12345, asOrganization='Example Network'), '8.8.8.8', 'Net.Coffee')
        self.assertEqual(value['trust_score'], 99)
        self.assertEqual(value['source'], 'Net.Coffee')
        for score in (True, -1, 101, '99', float('nan')):
            self.assertIsNone(env.intelligence(intel_result(trust_score=score), '8.8.8.8', 'Net.Coffee')['trust_score'])
        self.assertEqual(env.intelligence(intel_result('1.1.1.1'), '8.8.8.8', 'Net.Coffee')['status'], 'unknown')
        shared = env.intelligence(intel_result('8.8.8.9', cidr='8.8.8.0/24', trust_score=99), '8.8.8.8', 'Net.Coffee')
        self.assertEqual(shared['scope'], 'network')
        self.assertEqual(shared['ip'], '8.8.8.8')
        self.assertEqual(shared['cidr'], '8.8.8.0/24')
        self.assertEqual(env.intelligence(intel_result('1.1.1.1', cidr='0.0.0.0/0'), '8.8.8.8', 'Net.Coffee')['status'], 'unknown')
        obs = {k: env.trace(trace_result()) for k in env.TRACE_URLS}
        risk = env.intelligence(intel_result(trust_score=100, is_tor=True), '8.8.8.8', 'Net.Coffee')
        self.assertEqual(env.assess(obs, risk, {}, self.now, self.local)['level'], 'warning')

    def test_missing_dns_candidates_are_unknown_and_udp_private_candidates_are_ignored(self):
        self.monitor.manage('tick')
        self.requester.side_effect = lambda url: {'status': 200, 'body': json.dumps({'dns_servers': [], 'country_code': 'US'})}
        with patch.object(env.socket, 'getaddrinfo'), patch.object(env.time, 'sleep'):
            result = self.monitor.manage('dns')
        self.assertEqual(result['dns']['status'], 'unknown')
        result = self.monitor.manage('udp', ips=['127.0.0.1', '192.168.1.2', 'bad'])
        self.assertEqual(result['udp']['rows'], [])
        self.assertEqual(result['udp']['status'], 'unknown')
        self.now += 61
        result = self.monitor.manage('udp', ips=['1.1.1.1'])
        self.assertEqual(result['udp']['status'], 'different')

    def test_no_account_headers_and_failures_do_not_echo_credentials_or_html(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError('https://example.test/?secret=private', 403, 'private-token', {}, io.BytesIO(b'private-token'))
        with patch.object(env.urllib.request, 'build_opener', return_value=opener):
            result = env.request(env.TRACE_URLS['api'])
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'GET')
        self.assertNotIn('Authorization', request.headers)
        self.assertNotIn('Cookie', request.headers)
        self.assertIsNone(request.data)
        self.assertNotIn('private', json.dumps(result))
        self.assertNotIn('retry_seconds', result)
        with self.assertRaises(ValueError): env.request('http://127.0.0.1/secret')
        with self.assertRaises(ValueError): env.request('https://api.ipapi.is/?q=127.0.0.1')
        self.assertIsNone(env.NoRedirect().redirect_request(None, None, None, None, None, None))

    def test_symlink_cache_rejected_and_never_overwrites_target(self):
        self.monitor.prepare()
        target = Path(self.tmp.name) / 'untouched'
        target.write_text('untouched')
        (self.monitor.root / 'state.json').symlink_to(target)
        with self.assertRaises(ValueError): self.monitor.manage('refresh')
        self.assertEqual(target.read_text(), 'untouched')


if __name__ == '__main__': unittest.main()
