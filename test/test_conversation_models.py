import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'proxy'))
from conversation_models import native_id, normalize_response


class LegacyModelAliases(unittest.TestCase):
    models = [{'id':'claude-opus-4-8','name':'Claude Opus 4.8','overflow':True},
              {'id':'claude-opus-7','name':'Claude Opus 7'},
              {'id':'claude-sonnet-6','name':'Claude Sonnet 6'}]
    alias = 'claude-opus-99-01-20123456'

    def test_aliases_use_primary_native_id_without_adding_catalog_entries(self):
        before = copy.deepcopy(self.models)
        self.assertEqual(native_id(self.alias, self.models), 'claude-opus-7')
        self.assertEqual(native_id('claude-opus-4-8', self.models), 'claude-opus-4-8')
        self.assertEqual(native_id('claude-haiku-99-01-20123456', self.models), 'claude-sonnet-6')
        self.assertEqual(self.models, before)

    def test_frame_metadata_is_normalized_without_changing_user_content_or_source(self):
        payload = {'model':self.alias, 'context_data':{'_model':self.alias,'model':self.alias},
                   'input_data':{'model':self.alias},'output_data':{'model':self.alias},
                   'children':[{'model':self.alias}]}
        before = copy.deepcopy(payload)
        result = normalize_response(payload, '/api/frames/frame-one/trace-shallow', self.models)
        self.assertEqual(result['model'], 'claude-opus-7')
        self.assertEqual(result['context_data']['_model'], 'claude-opus-7')
        self.assertEqual(result['children'][0]['model'], 'claude-opus-7')
        self.assertEqual(result['context_data']['model'], self.alias)
        self.assertEqual(result['input_data'], before['input_data'])
        self.assertEqual(result['output_data'], before['output_data'])
        self.assertEqual(payload, before)

    def test_carried_preferences_and_reviewer_only_change_model_settings(self):
        payload = {'values':{'operon-default-model':self.alias,'operon-subagent-model':self.alias,'unrelated':self.alias}}
        result = normalize_response(payload,'/api/preferences/carried',self.models)
        self.assertEqual(result['values']['operon-default-model'],'claude-opus-7')
        self.assertEqual(result['values']['operon-subagent-model'],'claude-opus-7')
        self.assertEqual(result['values']['unrelated'],self.alias)
        self.assertEqual(normalize_response({'model':self.alias},'/api/preferences/reviewer-model',self.models)['model'],'claude-opus-7')
        self.assertEqual(normalize_response({'model':self.alias},'/api/other',self.models),{'model':self.alias})

if __name__ == '__main__': unittest.main()
