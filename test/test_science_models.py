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
            self.assertEqual([m['overflow'] for m in result['models']], [False, True, False])
            self.assertEqual([m['id'] for m in result['models']], ['claude-opus-6', 'claude-opus-5', 'claude-sonnet-6'])
            binary.write_bytes(binary.read_bytes().replace(b'opus-6', b'opus-7').replace(b'Opus 6', b'Opus 7') + b' ')
            self.assertEqual(models.discover(binary)['models'][0]['id'], 'claude-opus-7')
            result['models'][0]['id'] = 'tampered'
            self.assertEqual(models.discover(binary)['models'][0]['id'], 'claude-opus-7')

    def test_fallback_is_explicit_and_family_matching_is_strict(self):
        fallback = models.discover('/nonexistent-science')
        self.assertEqual(fallback['source'], 'fallback')
        self.assertEqual({m['family'] for m in fallback['models']}, {'opus', 'sonnet'})
        for mid, expected in [('claude-opus-6', 'opus'), ('claude-sonnet-5-20261001','sonnet'), ('claude-3-5-haiku-latest','haiku')]:
            self.assertEqual(models.family(mid), expected)
        for mid in ['my-opus-6', 'claude-opus-5-malicious', 'claude-unknown-5', 'gpt-6-astra']:
            self.assertIsNone(models.family(mid))

    def test_four_family_catalog_and_overflow_have_independent_targets(self):
        with tempfile.TemporaryDirectory() as root:
            binary = Path(root) / 'science'
            binary.write_bytes(b'catalog=[{id:"claude-fable-5-1",name:"Fable 5.1"},'
                b'{id:"claude-opus-5-5",name:"Claude Opus 5.5"},'
                b'{id:"claude-sonnet-5-5",name:"Claude Sonnet 5.5"},'
                b'{id:"claude-haiku-5-5",name:"Claude Haiku 5.5"},'
                b'{id:"claude-opus-5",name:"Claude Opus 5",overflow:!0}]')
            catalog = models.discover(binary)
        self.assertEqual([m['family'] for m in catalog['models']], ['fable', 'opus', 'sonnet', 'haiku', 'opus'])
        mappings = {m['id']: {'model': 'target-' + str(i), 'protocol': 'openai'}
                    for i, m in enumerate(catalog['models'])}
        with patch.object(models, 'discover', return_value=catalog):
            route = profile_from_config(self.config(model_mappings=mappings), proxy.PROVIDERS)
        token = proxy.CURRENT_ROUTE.set(route)
        try:
            for mid, mapping in mappings.items():
                self.assertEqual(proxy.resolve_model(mid), mapping['model'])
                self.assertEqual(proxy.resolve_model(mid + '-20261101'), mapping['model'])
                self.assertEqual(proxy.resolve_model(mid + '-latest'), mapping['model'])
            self.assertEqual(proxy.resolve_model('claude-fable-6'), 'gpt-6-astra')
        finally:
            proxy.CURRENT_ROUTE.reset(token)
        payload = {'models': {'anthropic': catalog['models']}}
        updated = models.adapt_model_response(payload, route['prov'])['models']['anthropic']
        self.assertEqual([m['id'] for m in updated], list(mappings))
        self.assertEqual([m['overflow'] for m in updated], [False, False, False, False, True])
        self.assertEqual([m['name'].split(' → ')[1] for m in updated], ['target-' + str(i) for i in range(5)])

    def test_invalid_mappings_fail_and_empty_mapping_inherits(self):
        for mapping in [[], {'gpt-model': {}}, {'claude-haiku-5-5': {'protocol': 'invalid'}},
                        {'claude-fable-5-1': {'model': 123}}]:
            with self.assertRaises(ValueError):
                profile_from_config(self.config(model_mappings=mapping), proxy.PROVIDERS)
        route = profile_from_config(self.config(model_mappings={'claude-fable-5-1': {'model': '', 'protocol': 'openai'}}), proxy.PROVIDERS)
        self.assertEqual(route['prov']['native_routes'], {})
        self.assertEqual(route['prov']['model_routes']['fable']['model'], 'gpt-6-astra')

    def test_configured_slots_replace_old_catalog_and_normalize_old_selections(self):
        from conversation_models import normalize_response
        native = {'models': [{'id': 'claude-opus-5', 'name': 'Claude Opus 5', 'family': 'opus'},
                  {'id': 'claude-sonnet-5', 'name': 'Claude Sonnet 5', 'family': 'sonnet'},
                  {'id': 'claude-sonnet-4-6', 'name': 'Claude Sonnet 4.6', 'family': 'sonnet', 'overflow': True}]}
        slots = {kind: {'model': kind + '-target', 'protocol': 'responses'} for kind in models.FAMILIES}
        with patch.object(models, 'discover', return_value=native):
            prov = profile_from_config(self.config(family_mappings=slots), proxy.PROVIDERS)['prov']
        self.assertEqual([m['family'] for m in prov['native_models']], list(models.FAMILIES))
        payload = {'models': {'anthropic': native['models']}, 'default_model_id': 'claude-sonnet-4-6'}
        result = models.adapt_model_response(payload, prov)
        self.assertEqual([m['name'] for m in result['models']['anthropic']],
                         [models.SLOT_LABELS[k] + ' → ' + k + '-target' for k in models.FAMILIES])
        self.assertTrue(all(not m['overflow'] for m in result['models']['anthropic']))
        self.assertEqual(result['default_model_id'], 'claude-sonnet-5')
        self.assertNotIn('csswitch_family_slot', json.dumps(result))
        from routing import select_model_route
        token = proxy.CURRENT_ROUTE.set({'prov': prov})
        try:
            for kind, entry in zip(models.FAMILIES, result['models']['anthropic']):
                target = proxy.resolve_model(entry['id'])
                self.assertEqual(target, kind + '-target')
                self.assertEqual(select_model_route({'prov': prov}, entry['id'], target)['prov']['resolved_model'], target)
        finally:
            proxy.CURRENT_ROUTE.reset(token)
        frame = {'model': 'claude-sonnet-4-6', 'context_data': {'_model': 'claude-haiku-5-5'},
                 'input_data': {'model': 'claude-sonnet-4-6'}}
        normalized = normalize_response(frame, '/api/frames/f1', prov['native_models'])
        self.assertEqual(normalized['model'], 'claude-sonnet-5')
        self.assertEqual(normalized['context_data']['_model'], 'claude-haiku-4-5')
        self.assertEqual(normalized['input_data'], frame['input_data'])
        fable_id = result['models']['anthropic'][0]['id']
        self.assertEqual(normalize_response({'model': fable_id}, '/api/frames/f1', prov['native_models'])['model'], fable_id)
        for flag in ['first_party_catalog', 'auth_error', 'fetch_error']:
            protected = {**payload, flag: True}
            self.assertIs(models.adapt_model_response(protected, prov), protected)

    def test_current_future_and_old_session_ids_use_configured_roles(self):
        route = profile_from_config(self.config(fast_model='obsolete', fast_protocol='invalid'), proxy.PROVIDERS)
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
        self.assertEqual(len(set(label for _, label in catalog)), len(route['prov']['native_models']))
        self.assertTrue(all(' → ' in label for _, label in catalog))
        self.assertNotIn('private-key', json.dumps(catalog))

    def test_stale_catalog_labels_update_without_overriding_native_selection_or_policy(self):
        route = profile_from_config(self.config(), proxy.PROVIDERS)['prov']
        mid = route['models'][0][0]
        payload = {'models': {'anthropic': [{'id': mid, 'name': 'Claude Opus 5 → old · 质量', 'description': 'CSSwitch 质量模型', 'disabled': True}]}, 'default_model_id': 'claude-sonnet-4-6'}
        updated = models.adapt_model_response(payload, route)
        self.assertEqual(updated['default_model_id'], 'claude-sonnet-4-6')
        self.assertTrue(updated['models']['anthropic'][0]['disabled'])
        self.assertIn('gpt-6-astra', updated['models']['anthropic'][0]['name'])
        self.assertEqual(len(updated['models']['anthropic']), len(route['native_models']))
        self.assertEqual(payload['models']['anthropic'][0]['name'], 'Claude Opus 5 → old · 质量')
        native = {'models': {'anthropic': [{'id': 'claude-opus-7', 'name': 'Claude Opus 7', 'disabled': True}]}}
        clean = models.adapt_model_response(native, route)
        self.assertEqual(len(clean['models']['anthropic']), 1)
        self.assertEqual(clean['models']['anthropic'][0]['id'], 'claude-opus-7')
        self.assertEqual(clean['models']['anthropic'][0]['name'], 'Claude Opus 7 → gpt-6-astra')
        self.assertTrue(clean['models']['anthropic'][0]['disabled'])
        self.assertEqual(native['models']['anthropic'][0]['name'], 'Claude Opus 7')
        for flags in [{'first_party_catalog': True}, {'model_list_source': 'selector'}, {'auth_error': 'sign in'}, {'fetch_error':'offline'}]:
            original = {**payload, **flags}
            self.assertIs(models.adapt_model_response(original, route), original)

    def test_legacy_aliases_never_add_menu_entries_and_overflow_is_native(self):
        route = profile_from_config(self.config(), proxy.PROVIDERS)['prov']
        alias = 'claude-sonnet-99-20-20687152'
        payload = {'models': {'anthropic': [{'id': alias, 'name': 'Sonnet', 'description': '由 CSSwitch 模型映射控制'}]}, 'default_model_id': alias}
        result = models.adapt_model_response(payload, route)
        items = result['models']['anthropic']
        self.assertEqual([m['id'] for m in items], [m['id'] for m in route['native_models']])
        self.assertEqual([m.get('overflow', False) for m in items], [m.get('overflow', False) for m in route['native_models']])
        self.assertNotIn(alias, [m['id'] for m in items])
        self.assertTrue(result['default_model_id'].startswith('claude-sonnet-'))
        self.assertNotIn('-99-', result['default_model_id'])
        self.assertTrue(all(' → ' in m['name'] for m in items))


if __name__ == '__main__':
    unittest.main()
