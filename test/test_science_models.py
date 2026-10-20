import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'proxy'))
import science_models as models
from routing import profile_from_config
import csswitch_proxy as proxy


class ModelMapping(unittest.TestCase):
    def config(self, **changes):
        return {'provider': 'test', 'providers': {'test': {'base_url': 'https://example.invalid', 'protocol': 'responses',
                'key': 'private-key', 'model': 'gpt-6.1-sol', 'quality_model': 'gpt-6-astra', 'fast_model': '', **changes}}}

    def test_discovery_follows_catalog_and_default_not_future_capacity_table(self):
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root)/'science'
            binary.write_bytes(b'catalog=[{id:"claude-opus-6",name:"Claude Opus 6"},{id:"claude-opus-5",name:"Claude Opus 5",overflow:!0},{id:"claude-sonnet-6",name:"Claude Sonnet 6"}];fast="claude-haiku-5-20261001";capacity={"claude-haiku-99":1000000}')
            result = models.discover(binary)
            self.assertEqual(result['source'], 'installed')
            self.assertEqual([m['id'] for m in result['models']], ['claude-opus-6', 'claude-sonnet-6', 'claude-haiku-5'])
            binary.write_bytes(binary.read_bytes().replace(b'opus-6', b'opus-7').replace(b'Opus 6', b'Opus 7') + b' ')
            self.assertEqual(models.discover(binary)['models'][0]['id'], 'claude-opus-7')
            result['models'][0]['id'] = 'tampered'
            self.assertEqual(models.discover(binary)['models'][0]['id'], 'claude-opus-7')

    def test_fallback_is_explicit_and_family_matching_is_strict(self):
        self.assertEqual(models.discover('/nonexistent-science')['source'], 'fallback')
        for mid, expected in [('claude-opus-6', 'opus'), ('claude-sonnet-5-20261001','sonnet'), ('claude-3-5-haiku-latest','haiku')]:
            self.assertEqual(models.family(mid), expected)
        for mid in ['my-opus-6', 'claude-opus-5-malicious', 'claude-unknown-5', 'gpt-6-astra']:
            self.assertIsNone(models.family(mid))

    def test_current_future_and_old_session_ids_use_configured_roles(self):
        route = profile_from_config(self.config(), proxy.PROVIDERS)
        token = proxy.CURRENT_ROUTE.set(route)
        try:
            for mid in ['claude-opus-5', 'claude-opus-6', 'claude-opus-4-8', 'claude-3-opus-20240229']:
                self.assertEqual(proxy.resolve_model(mid), 'gpt-6-astra')
            for mid in ['claude-sonnet-6','claude-haiku-5','claude-haiku-4-5-20251001']:
                self.assertEqual(proxy.resolve_model(mid), 'gpt-6.1-sol')
            self.assertEqual(proxy.resolve_model('gpt-6-astra'), 'gpt-6-astra')
        finally:
            proxy.CURRENT_ROUTE.reset(token)
        catalog = route['prov']['models']
        self.assertEqual(len(set(label for _, label in catalog)), 3)
        self.assertTrue(all(' → ' in label for _, label in catalog))
        self.assertNotIn('private-key', json.dumps(catalog))

    def test_stale_catalog_labels_update_without_overriding_native_selection_or_policy(self):
        route = profile_from_config(self.config(), proxy.PROVIDERS)['prov']
        mid = route['models'][0][0]
        payload = {'models': {'anthropic': [{'id': mid, 'name': 'old', 'disabled': True}]}, 'default_model_id': 'claude-sonnet-4-6'}
        updated = models.adapt_model_response(payload, route)
        self.assertEqual(updated['default_model_id'], 'claude-sonnet-4-6')
        self.assertTrue(updated['models']['anthropic'][0]['disabled'])
        self.assertIn('gpt-6-astra', updated['models']['anthropic'][0]['name'])
        self.assertEqual(payload['models']['anthropic'][0]['name'], 'old')
        for flags in [{'first_party_catalog': True}, {'model_list_source': 'selector'}, {'auth_error': 'sign in'}, {'fetch_error':'offline'}]:
            original = {**payload, **flags}
            self.assertIs(models.adapt_model_response(original, route), original)


if __name__ == '__main__':
    unittest.main()
