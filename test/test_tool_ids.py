import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'proxy'))
from tool_ids import normalize, VALID


class ToolIds(unittest.TestCase):
    def test_long_ids_and_results_remain_paired_without_changing_history(self):
        ids = ['toolu_' + 'x' * 60, 'toolu_' + 'x' * 59 + 'y', 'a valid/foreign ID', 'already-valid']
        messages = [{'role': 'assistant', 'content': [{'type': 'tool_use', 'id': i, 'name': 'search', 'input': {}} for i in ids]},
                    {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': i, 'content': 'OK'} for i in ids]}]
        before = copy.deepcopy(messages)
        result = normalize(messages)
        calls = [b['id'] for b in result[0]['content']]
        self.assertEqual(calls, [b['tool_use_id'] for b in result[1]['content']])
        self.assertTrue(all(VALID.fullmatch(i) for i in calls))
        self.assertEqual(len(set(calls)), len(ids))
        self.assertEqual(calls[-1], ids[-1])
        self.assertEqual(normalize(messages), result)
        self.assertEqual(normalize(result), result)
        self.assertEqual(messages, before)

    def test_existing_valid_id_cannot_collide_with_a_shortened_id(self):
        foreign = 'x' * 66
        collision = 'call_cs_' + hashlib.sha256(foreign.encode()).hexdigest()[:56]
        result = normalize([{'content': [{'type': 'tool_use', 'id': i} for i in [foreign, collision]]}])
        self.assertNotEqual(result[0]['content'][0]['id'], collision)
        self.assertEqual(result[0]['content'][1]['id'], collision)

    def test_text_images_and_tool_payloads_are_not_rewritten(self):
        message = [{'role': 'user', 'content': [{'type': 'text', 'text': 'toolu_' + 'x'*60},
                    {'type': 'image', 'source': {'type': 'url', 'url': 'https://example.invalid/image'}}]}]
        self.assertEqual(normalize(message), message)
        for value in [None, 2, '']:
            with self.assertRaises(ValueError):
                normalize([{'content': [{'type': 'tool_result', 'tool_use_id': value}]}])


if __name__ == '__main__': unittest.main()
