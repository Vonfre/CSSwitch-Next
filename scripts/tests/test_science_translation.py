import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'proxy')]
import science_translation as translation
import translatejs_edge


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name)/'config.json'
        self.cfg = {'science_zh':True, 'science_zh_online':True, 'provider':'fixture', 'providers':{
            'fixture':{'base_url':'https://example.invalid', 'key':'PRIVATE-KEY', 'protocol':'responses',
                       'model':'default-model', 'fast_model':'fast-model', 'fast_protocol':'anthropic'}}}
        self.save()
        self.service = translation.Translator(self.config)
        self.service.observe_asset(b'const empty=""; const labels={children:"Show archived sessions", title:"Future interface label"};')

    def save(self):
        self.config.write_text(json.dumps(self.cfg)); self.config.chmod(0o600)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_shipped_literals_leave_machine_and_cache_survives_new_entry(self):
        texts = ['Show archived sessions', 'My private project title']
        with patch.object(self.service, 'request', return_value={texts[0]:'显示已归档的会话'}) as request:
            result = self.service.translate(texts)
        request.assert_called_once_with(texts[:1])
        self.assertEqual(result['translations'], {texts[0]:'显示已归档的会话'})
        self.assertEqual(result['skipped'], texts[1:])
        second = translation.Translator(self.config)
        second.observe_asset(b'const label="Show archived sessions"')
        with patch.object(second, 'request') as request:
            self.assertEqual(second.translate(texts[:1])['translations'], result['translations'])
        request.assert_not_called()
        raw = second.path.read_text()
        self.assertNotIn(texts[1], raw); self.assertNotIn('PRIVATE-KEY', raw)
        self.assertEqual(second.path.stat().st_mode & 0o777, 0o600)

    def test_disabled_feature_and_invalid_inputs_never_call_network(self):
        self.cfg['science_zh_online'] = False; self.save()
        with patch.object(self.service, 'request') as request:
            with self.assertRaises(translation.TranslationError): self.service.translate(['Show archived sessions'])
            for texts in (['https://secret.invalid'], ['sk-sensitive'], ['person@example.com'], ['/private/document'], ['x'*301], [None], 'text'):
                with self.subTest(texts=texts), self.assertRaises(translation.TranslationError): self.service.translate(texts)
        request.assert_not_called()

    def test_failure_backoff_is_shared_and_does_not_cache_failed_result(self):
        with patch.object(self.service, 'request', side_effect=RuntimeError('PRIVATE-KEY upstream body')) as request:
            for _ in range(2):
                with self.assertRaises(translation.TranslationError) as error: self.service.translate(['Show archived sessions'])
                self.assertNotIn('PRIVATE-KEY', str(error.exception))
        self.assertEqual(request.call_count, 1)
        self.assertEqual(self.service.cache(), {})
        self.assertEqual(self.service.read()['daily_count'], 1)

    def test_edge_adapter_batch_shape_and_no_model_credentials(self):
        class Opener:
            def open(inner, req, timeout):
                self.assertEqual(req.full_url, translatejs_edge.ENDPOINT)
                self.assertEqual(json.loads(req.data), ['Show archived sessions'])
                self.assertNotIn('Authorization', req.headers)
                return io.BytesIO(json.dumps([{'translations':[{'text':'显示已归档的会话'}]}]).encode())
        self.assertEqual(translatejs_edge.translate(['Show archived sessions'], Opener()), ['显示已归档的会话'])

    def test_api_uses_default_model_ignores_retired_fast_and_records_no_prompt(self):
        self.cfg['science_zh_engine'] = 'api'; self.save()
        class Opener:
            def open(inner, req, timeout):
                payload = json.loads(req.data)
                self.assertTrue(req.full_url.endswith('/v1/responses'))
                self.assertEqual(payload['model'], 'default-model')
                self.assertEqual(json.loads(payload['input'][-1]['content'][0]['text']), ['Show archived sessions'])
                return io.BytesIO(json.dumps({'output':[{'type':'message','content':[{'type':'output_text','text':'{"translations":["显示已归档的会话"]}'}]}],
                                             'usage':{'input_tokens':30,'output_tokens':8}}).encode())
        with patch.object(translation.urllib.request, 'build_opener', return_value=Opener()):
            result = self.service.translate(['Show archived sessions'])
        self.assertEqual(result['translations']['Show archived sessions'], '显示已归档的会话')
        journal = (self.config.parent/'usage.jsonl').read_text()
        self.assertIn('default-model', journal); self.assertNotIn('fast-model', journal)
        self.assertNotIn('Show archived sessions', journal); self.assertNotIn('PRIVATE-KEY', journal)

    def test_invalid_ai_output_never_enters_cache(self):
        self.cfg['science_zh_engine'] = 'api'; self.save()
        class Opener:
            def open(inner, req, timeout):
                return io.BytesIO(b'{"content":[{"type":"text","text":"{\\"translations\\":[\\"<script>bad</script>\\"]}"}]}')
        with patch.object(translation.urllib.request, 'build_opener', return_value=Opener()):
            with self.assertRaises(translation.TranslationError):self.service.translate(['Show archived sessions'])
        self.assertEqual(self.service.cache(), {})
        self.assertFalse(translation.valid_translation('Delete {count} files', '删除文件'))
        self.assertTrue(translation.valid_translation('Delete {count} files', '删除 {count} 个文件'))


if __name__ == '__main__':
    unittest.main()
