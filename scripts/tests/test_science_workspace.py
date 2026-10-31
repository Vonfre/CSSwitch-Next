import base64
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
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
                db.execute('INSERT INTO frame_messages VALUES (?,?,?)', ('root1', i, json.dumps({'role': 'user' if i % 2 == 0 else 'assistant', 'timestamp': 1000+i, 'content': [{'type': 'text', 'text': '<script>unsafe</script>'}, {'type': 'tool_use', 'name': 'search', 'input': {'query': 'paper'}}]})))
        self.work = w.Workspace(self.home, self.data, self.config)
        db.close()
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
        self.assertIn('<script>', self.work.sessions()['sessions'][0]['last_preview'])
        self.assertEqual(first['messages'][0]['timestamp'], 1000)
        last = self.work.run('session_messages', {'key': 'org-fixture:root1', 'offset': 100})
        self.assertEqual(len(last['messages']), 3)
        self.assertFalse(last['has_more'])

    def test_tool_pairing_ids_and_error_state_survive_read_only_conversion(self):
        blocks = w.message_blocks([
            {'type': 'tool_use', 'id': 'call-1', 'name': 'Bash', 'input': {'command': 'false'}},
            {'type': 'tool_result', 'tool_use_id': 'call-1', 'is_error': True, 'content': 'exit 1'},
        ])
        self.assertEqual(blocks[0]['id'], blocks[1]['tool_use_id'])
        self.assertTrue(blocks[1]['is_error'])
        self.assertIn('false', blocks[0]['text'])
        self.assertEqual(blocks[1]['text'], 'exit 1')

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
        try: (p / 'secret').symlink_to(outside)
        except OSError as e:
            if getattr(e, 'winerror', None) == 1314: self.skipTest('Windows symlink privilege is unavailable')
            raise
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
        for filename, mode in [('../escape', 0), ('/absolute', 0), ('repo/link', 0o120777),
                               ('C:/outside', 0), ('repo/file:stream', 0), ('repo/CON.txt', 0)]:
            content = io.BytesIO()
            with zipfile.ZipFile(content, 'w') as z:
                info = zipfile.ZipInfo(filename); info.external_attr = mode << 16; z.writestr(info, 'data')
            for repository in (False, True):
                with zipfile.ZipFile(content) as z, self.assertRaises(w.WorkspaceError):
                    w.safe_zip(z, repository=repository)

    def test_large_repository_streams_to_disk_and_only_packages_selected_skill(self):
        archive = self.root / 'large.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('repo-main/paper/SKILL.md', '---\nname: paper\ndescription: Research\n---\n')
            z.writestr('repo-main/paper/references/readme.md', 'Reference')
            # The download exceeds 16 MiB even without relying on compression ratios.
            with z.open('repo-main/unrelated.bin', 'w') as stream:
                for _ in range(17): stream.write(b'x' * 1024 * 1024)
            info = zipfile.ZipInfo('repo-main/large-dataset.bin')
            info.compress_type = zipfile.ZIP_DEFLATED
            with z.open(info, 'w') as stream:
                for _ in range(65): stream.write(b'\0' * 1024 * 1024)
            for i in range(12001): z.writestr(f'repo-main/docs/{i}.txt', '')
        self.assertGreater(archive.stat().st_size, 16 * 1024 * 1024)
        # Spaces / Unicode and real file replacement also run on the Windows CI runner.
        self.config = self.root / 'repository cache 中文'
        self.work = w.Workspace(self.home, self.data, self.config)
        cached = self.work.repo_path('org/repo@main')
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'previous cache')
        with archive.open('rb') as downloaded:
            response = Mock()
            response.__enter__ = Mock(return_value=response)
            response.__exit__ = Mock(return_value=False)
            response.read.side_effect = downloaded.read
            with patch.object(w.urllib.request, 'urlopen', return_value=response):
                self.work.repo_add({'repo': 'https://github.com/org/repo', 'branch': 'main'})
            self.assertGreater(response.read.call_count, 16)
            self.assertTrue(all(call.args == (w.REPO_DOWNLOAD_CHUNK,) for call in response.read.call_args_list))
        self.assertEqual(cached.stat().st_size, archive.stat().st_size)
        # Repositories are scanned in place, not extracted to the cache directory.
        self.assertEqual(list(cached.parent.iterdir()), [cached])
        original_read = zipfile.ZipFile.read
        def bounded_read(z, name, *args, **kwargs):
            filename = name.filename if isinstance(name, zipfile.ZipInfo) else name
            self.assertIn('/paper/', filename)
            return original_read(z, name, *args, **kwargs)
        with patch.object(zipfile.ZipFile, 'read', bounded_read):
            skill = self.work.discover()['skills'][0]
            result = self.work.skill_bundle(skill['key'])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(result['bundle']))) as z:
            self.assertEqual(set(z.namelist()), {'paper/SKILL.md', 'paper/references/readme.md'})
        cached.rename(cached.with_suffix('.closed.zip'))

    def test_failed_repository_sync_keeps_old_cache_and_cleans_partial_download(self):
        path = self.work.repo_path('org/repo@main')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'old cache')
        w.write_json(self.work.pref, {'repos': [{'key': 'org/repo@main', 'repo': 'org/repo', 'branch': 'main'}]})
        prefs = self.work.pref.read_bytes()
        for failure in [TimeoutError(), ConnectionResetError(), w.http.client.IncompleteRead(b'partial'), OSError('disk full')]:
            with self.subTest(failure=type(failure).__name__):
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.read.side_effect = [b'partial', failure]
                with patch.object(w.urllib.request, 'urlopen', return_value=response), self.assertRaises(w.WorkspaceError):
                    self.work.repo_add({'repo': 'org/repo'})
                self.assertEqual(path.read_bytes(), b'old cache')
                self.assertEqual(self.work.pref.read_bytes(), prefs)
                self.assertEqual(list(path.parent.iterdir()), [path])

    def test_locked_cache_replacement_preserves_the_previous_download(self):
        path = self.work.repo_path('org/repo@main')
        path.parent.mkdir(parents=True)
        path.write_bytes(b'previous cache')
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as z: z.writestr('repo-main/README.md', 'Valid archive')
        content.seek(0)
        with patch.object(w.urllib.request, 'urlopen', return_value=content), \
                patch.object(w.os, 'replace', side_effect=PermissionError('File is in use')), \
                self.assertRaisesRegex(w.WorkspaceError, '缓存写入失败'):
            self.work.repo_add({'repo': 'org/repo'})
        self.assertEqual(path.read_bytes(), b'previous cache')
        self.assertEqual(list(path.parent.iterdir()), [path])
        self.assertEqual(self.work.repos(), [])

    def test_invalid_repository_zip_is_not_registered_or_cached(self):
        for data in [b'not a zip', self.unsafe_repository()]:
            with patch.object(w.urllib.request, 'urlopen', return_value=io.BytesIO(data)), self.assertRaises(w.WorkspaceError):
                self.work.repo_add({'repo': 'org/repo'})
            self.assertEqual(self.work.repos(), [])
            self.assertEqual(list(self.work.repo_path('org/repo@main').parent.iterdir()), [])

    def unsafe_repository(self):
        content = io.BytesIO()
        with zipfile.ZipFile(content, 'w') as z: z.writestr('../escape', 'unsafe')
        return content.getvalue()

    def test_repository_download_deadline_cleans_partial_file(self):
        with patch.object(w.urllib.request, 'urlopen', return_value=io.BytesIO(b'partial')), \
                patch.object(w.time, 'monotonic', side_effect=[0, w.REPO_DOWNLOAD_SECONDS + 1]), \
                self.assertRaisesRegex(w.WorkspaceError, '下载超时'):
            self.work.repo_add({'repo': 'org/repo'})
        self.assertEqual(list(self.work.repo_path('org/repo@main').parent.iterdir()), [])

    def test_repository_bridge_has_time_to_finish_and_cleanup_on_both_platforms(self):
        request = json.dumps({'home': str(self.home), 'data': str(self.data), 'config': str(self.config),
                              'operation': 'repo_add', 'params': {'repo': 'org/repo'}})
        initial, download = Mock(), Mock()
        with patch.object(w.sys, 'stdin', io.StringIO(request)), patch.object(w.sys, 'stdout', io.StringIO()), \
                patch.object(w.Workspace, 'run', return_value={}), \
                patch.object(w.agent_skills._platform, 'deadline', side_effect=[initial, download]) as deadline:
            w.main()
        self.assertEqual([call.args for call in deadline.call_args_list], [(55,), (w.REPO_DOWNLOAD_SECONDS + 60,)])
        initial.cancel.assert_called_once()
        download.cancel.assert_called_once()

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
        try: self.work.pref.symlink_to(outside)
        except OSError as e:
            if getattr(e, 'winerror', None) == 1314: self.skipTest('Windows symlink privilege is unavailable')
            raise
        with self.assertRaises(w.WorkspaceError): self.work.preferences()
        with self.assertRaises(w.WorkspaceError): w.write_json(self.work.pref, {})
        self.assertEqual(outside.read_text(), '{}')


if __name__ == '__main__': unittest.main()
