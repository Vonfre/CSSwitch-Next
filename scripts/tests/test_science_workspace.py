import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('science_workspace', Path(__file__).parents[1] / 'science-workspace.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / 'home'
        self.data = self.root / 'science'
        self.config = self.root / 'config'
        self.dbpath = self.data / 'orgs/org-fixture/operon-cli.db'
        self.dbpath.parent.mkdir(parents=True)
        with sqlite3.connect(self.dbpath) as db:
            db.executescript('CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT); CREATE TABLE frames(id TEXT PRIMARY KEY, project_id TEXT, parent_frame_id TEXT, name TEXT, model TEXT, status TEXT, created_at INTEGER, updated_at INTEGER); CREATE TABLE frame_messages(frame_id TEXT, idx INTEGER, msg_json TEXT);')
            db.execute('INSERT INTO projects VALUES (?,?)', ('p1', '研究项目'))
            db.execute('INSERT INTO frames VALUES (?,?,?,?,?,?,?,?)', ('root1', 'p1', None, '论文分析', 'test-model', 'completed', 1000, 2000))
            db.execute('INSERT INTO frames VALUES (?,?,?,?,?,?,?,?)', ('child1', 'p1', 'root1', '子代理', 'test-model', 'completed', 1000, 1500))
            for i in range(103):
                db.execute('INSERT INTO frame_messages VALUES (?,?,?)', ('root1', i, json.dumps({'role': 'user' if i % 2 == 0 else 'assistant', 'content': [{'type': 'text', 'text': '<script>unsafe</script>'}, {'type': 'tool_use', 'name': 'search', 'input': {'query': 'paper'}}]})))
        self.work = w.Workspace(self.home, self.data, self.config)
        self.digest = hashlib.sha256(self.dbpath.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.dbpath.read_bytes()).hexdigest(), self.digest, 'Science DB must never be mutated')
        self.temp.cleanup()

    def test_history_only_lists_root_sessions_and_counts_messages(self):
        rows = self.work.run('sessions_list', {})['sessions']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['messages'], 103)
        self.assertEqual(rows[0]['project_name'], '研究项目')
        self.assertEqual(rows[0]['title'], '论文分析')

    def test_pagination_and_tool_content_are_preserved(self):
        first = self.work.run('session_messages', {'key': 'org-fixture:root1'})
        self.assertEqual(len(first['messages']), 100)
        self.assertTrue(first['has_more'])
        self.assertIn('[工具调用：search]', first['messages'][0]['text'])
        self.assertIn('<script>', first['messages'][0]['text'])
        self.assertEqual(first['messages'][0]['blocks'][1]['type'], 'tool_use')
        self.assertEqual(first['messages'][0]['blocks'][1]['name'], 'search')
        self.assertIn('<script>', self.work.sessions()['sessions'][0]['preview'])
        last = self.work.run('session_messages', {'key': 'org-fixture:root1', 'offset': 100})
        self.assertEqual(len(last['messages']), 3)
        self.assertFalse(last['has_more'])

    def test_meta_only_changes_csswitch_preferences(self):
        key = 'org-fixture:root1'
        self.work.run('session_meta', {'key': key, 'changes': {'title': '新名字', 'starred': True, 'archived': True}})
        row = self.work.sessions()['sessions'][0]
        self.assertEqual(row['title'], '新名字')
        self.assertTrue(row['archived'])
        self.assertTrue(row['starred'])
        self.work.run('session_meta', {'key': key, 'changes': {'starred': False}})
        self.assertEqual(self.work.sessions()['sessions'][0]['title'], '新名字')

    def test_path_traversal_child_and_missing_session_rejected(self):
        for key in ['../org:root1', 'org-fixture:../../root', 'org-fixture:child1', 'org-fixture:missing']:
            with self.subTest(key=key), self.assertRaises(w.WorkspaceError):
                self.work.run('session_messages', {'key': key})

    def test_corrupt_database_is_reported_without_hiding_healthy_org(self):
        p = self.data / 'orgs/broken/operon-cli.db'; p.parent.mkdir(); p.write_bytes(b'not sqlite')
        value = self.work.sessions()
        self.assertEqual(len(value['sessions']), 1)
        self.assertEqual(len(value['warnings']), 1)

    def local_skill(self):
        p = self.home / '.codex/skills/paper-helper'
        p.mkdir(parents=True)
        (p / 'SKILL.md').write_text('---\nname: paper-helper\ndescription: >\n  Analyze papers\n  with references.\n---\n# Helper\n', encoding='utf8')
        (p / 'references').mkdir()
        (p / 'references/method.md').write_text('Real reference')
        return p

    def test_skill_import_packages_supporting_files_and_ignores_symlinks(self):
        p = self.local_skill()
        outside = self.root / 'outside'; outside.write_text('must-not-be-included')
        (p / 'secret').symlink_to(outside)
        (p.parent / 'linked-skill').symlink_to(p)
        (self.home / '.codex/skills/.ignored').mkdir()
        found = self.work.discover()['skills']
        self.assertEqual(len(found), 1)
        self.assertIn('Analyze papers with references.', found[0]['description'])
        value = self.work.skill_bundle('codex:paper-helper')
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(value['bundle']))) as z:
            self.assertEqual(set(z.namelist()), {'paper-helper/SKILL.md', 'paper-helper/references/method.md'})

    def test_skill_bundle_size_limit(self):
        p = self.local_skill(); (p / 'big').write_bytes(b'x' * (w.MAX_BUNDLE + 1))
        with self.assertRaises(w.WorkspaceError): self.work.skill_bundle('codex:paper-helper')

    def test_batch_archive_prevalidates_skills_and_preserves_support_files(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as z:
            for name in ('one', 'two'):
                z.writestr(f'pack/{name}/SKILL.md', f'---\nname: {name}\ndescription: Fixture\n---\n# Skill')
                z.writestr(f'pack/{name}/references/readme.md', 'Reference')
            z.writestr('pack/README.md', 'Collection')
        plan = self.work.run('skill_import_plan', {'filename':'collection.zip','bundle':base64.b64encode(content.getvalue()).decode()})
        self.assertEqual({s['name'] for s in plan['skills']}, {'one','two'})
        for skill in plan['skills']:
            name, _, files = w.local_skill_files.unpack(skill['filename'], skill['bundle'])
            self.assertEqual(name,skill['name'])
            self.assertEqual(files['references/readme.md'],b'Reference')

    def test_batch_archive_rejects_special_files_and_published_markers(self):
        for path, mode in [('pack/test/device',0o020600), ('pack/test/.catalog_stamp',0), ('../escape',0)]:
            content=io.BytesIO()
            with zipfile.ZipFile(content,'w') as z:
                z.writestr('pack/test/SKILL.md','---\nname: test\ndescription: Fixture\n---\n')
                info=zipfile.ZipInfo(path);info.external_attr=mode<<16;z.writestr(info,b'unsafe')
            with self.assertRaises(w.WorkspaceError):
                self.work.import_plan({'filename':'test.zip','bundle':base64.b64encode(content.getvalue()).decode()})

    def test_source_bundle_hash_matches_the_installed_package_and_detects_reference_edits(self):
        p=self.local_skill()
        first=self.work.skill_bundle('codex:paper-helper')
        _,_,files=w.local_skill_files.unpack(first['filename'],first['bundle'])
        self.assertEqual(first['fingerprint'],w.local_skill_files.fingerprint(files))
        self.assertEqual(first['sourceKey'],'codex:paper-helper')
        (p/'references/method.md').write_text('Updated reference')
        self.assertNotEqual(self.work.skill_bundle('codex:paper-helper')['fingerprint'], first['fingerprint'])

    def test_github_discovery_packages_only_selected_skill(self):
        repo = {'key': 'org/repo@main', 'repo': 'org/repo', 'branch': 'main'}
        w.write_json(self.work.pref, {'repos': [repo]})
        path = self.work.repo_path(repo['key']); path.parent.mkdir()
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('repo-main/skills/paper/SKILL.md', '---\nname: paper\ndescription: Research\n---\n')
            z.writestr('repo-main/skills/paper/scripts/run.py', '# inert')
            z.writestr('repo-main/unrelated.txt', 'not included')
        skill = self.work.discover()['skills'][0]
        value = self.work.skill_bundle(skill['key'])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(value['bundle']))) as z:
            self.assertEqual(set(z.namelist()), {'paper/SKILL.md', 'paper/scripts/run.py'})
        self.work.run('repo_remove', {'key': repo['key']})
        self.assertEqual(self.work.discover()['skills'], [])

    def test_unsafe_zip_paths_and_symlinks_rejected(self):
        for filename, mode in [('../escape', 0), ('/absolute', 0), ('repo/link', 0o120777)]:
            content = io.BytesIO()
            with zipfile.ZipFile(content, 'w') as z:
                info = zipfile.ZipInfo(filename); info.external_attr = mode << 16; z.writestr(info, 'data')
            with zipfile.ZipFile(content) as z, self.assertRaises(w.WorkspaceError): w.safe_zip(z)

    def test_github_repository_urls_are_validated_before_network(self):
        for repo in ['https://evil.example/repo', '../../escape', 'org/repo/path', 'org/..']:
            with self.subTest(repo=repo), patch.object(w.urllib.request, 'urlopen') as request, self.assertRaises(w.WorkspaceError):
                self.work.repo_add({'repo': repo})
                request.assert_not_called()

    def test_pricing_persists_and_rejects_invalid_values(self):
        rates = dict(input=2.5, output=10, cache_read=0.25, cache_write=3)
        self.work.run('pricing_save', {'model': 'test-model', 'rates': rates})
        self.assertEqual(self.work.run('pricing_get', {})['pricing']['test-model'], rates)
        for value in [-1, float('nan'), float('inf'), True, '5']:
            with self.subTest(value=value), self.assertRaises(w.WorkspaceError):
                self.work.run('pricing_save', {'model': 'test-model', 'rates': {**rates, 'input': value}})

    def test_symlinked_preferences_never_overwrite_target(self):
        self.config.mkdir(); outside = self.root / 'protected'; outside.write_text('{}')
        self.work.pref.symlink_to(outside)
        with self.assertRaises(w.WorkspaceError): self.work.preferences()
        with self.assertRaises(w.WorkspaceError): w.write_json(self.work.pref, {})
        self.assertEqual(outside.read_text(), '{}')


if __name__ == '__main__': unittest.main()
