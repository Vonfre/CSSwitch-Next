from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parents[1]))
import science_local_files as files


class LocalFilesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.current = self.root / '文件 with spaces.md'; self.current.write_text('current')
        self.old = self.root / 'deduplicated old.md'; self.old.write_text('old')
        self.payload = {'artifactId': 'art_1', 'versionId': 'v2', 'action': 'reveal'}
        self.meta = {'version_id': 'v2', 'ref_host_path': None, 'file_path': str(self.current)}
        self.request = Mock(side_effect=lambda url: self.meta if url.endswith('/metadata') else [
            {'artifact_id': 'art_1', 'version_id': 'v1', 'file_path': str(self.old)}])
    def tearDown(self):
        self.temp.cleanup()
    def test_current_storage_is_used_without_guessing_or_downloading(self):
        path, _ = files.resolve_artifact(self.payload, self.request)
        self.assertEqual(path, self.current.resolve())
        self.request.assert_called_once_with('/api/artifacts/art_1/metadata')
    def test_historical_version_uses_its_own_storage(self):
        self.payload['versionId'] = 'v1'
        self.assertEqual(files.resolve_artifact(self.payload, self.request)[0], self.old.resolve())
        self.assertEqual(self.request.call_count, 2)
    def test_missing_ambiguous_or_wrong_artifact_version_never_uses_latest(self):
        self.payload['versionId'] = 'v1'
        good = {'artifact_id': 'art_1', 'version_id': 'v1', 'file_path': str(self.old)}
        for versions in [[], [good, good], [{**good, 'artifact_id': 'other'}], [{**good, 'file_path': None}], {}]:
            with self.subTest(versions=versions), self.assertRaises(files.FileActionError):
                files.resolve_artifact(self.payload, Mock(side_effect=[self.meta, versions]))
    def test_current_original_path_fallback(self):
        self.meta.update(file_path=None, ref_host_path=str(self.current))
        self.assertEqual(files.resolve_artifact(self.payload, self.request)[0], self.current.resolve())
    def test_invalid_payload_and_raw_path_are_rejected_before_request(self):
        for payload in [[], None, {}, {**self.payload, 'path': str(self.current)},
                        {**self.payload, 'artifactId': '../private'}, {**self.payload, 'artifactId': []},
                        {**self.payload, 'versionId': 4}, {**self.payload, 'action': 'shell'}, {**self.payload, 'action': []}]:
            with self.subTest(payload=payload), self.assertRaises(files.FileActionError):
                files.resolve_artifact(payload, self.request)
        self.request.assert_not_called()
    def test_remote_missing_directory_and_device_paths_rejected(self):
        for path in ['https://example.test/a.md', '//server/share.md', 'relative.md', str(self.root), '/dev/null',
                     str(self.root / 'missing.md'), '/bad\x00path', None]:
            self.meta['file_path'] = path
            with self.subTest(path=path), self.assertRaises(files.FileActionError):
                files.resolve_artifact(self.payload, self.request)
    def test_existing_symlink_resolves_to_recorded_file(self):
        link = self.root / 'link.md'; link.symlink_to(self.current)
        self.meta['file_path'] = str(link)
        self.assertEqual(files.resolve_artifact(self.payload, self.request)[0], self.current.resolve())
    @patch.object(files.sys, 'platform', 'darwin')
    @patch.object(files.subprocess, 'run')
    def test_os_modes_are_argument_arrays_and_never_shell(self, run):
        for action, flags in [('open', []), ('edit', ['-t']), ('reveal', ['-R'])]:
            self.payload['action'] = action
            files.open_artifact(self.payload, self.request)
            run.assert_called_with(['/usr/bin/open', *flags, str(self.current.resolve())], check=True,
                timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(run.call_count, 3)
    @patch.object(files.sys, 'platform', 'darwin')
    @patch.object(files.subprocess, 'run')
    def test_executable_default_open_is_blocked_but_explicit_editor_and_reveal_work(self, run):
        for filename in ['launch.command', 'script.py', 'link.webloc', 'archive.dmg', 'app.app']:
            path = self.root / filename; path.write_text('fixture')
            self.meta['file_path'] = str(path); self.payload['action'] = 'open'
            with self.assertRaises(files.FileActionError): files.open_artifact(self.payload, self.request)
        run.assert_not_called()
        self.meta['file_path'] = str(self.current); self.current.chmod(0o700)
        with self.assertRaises(files.FileActionError): files.open_artifact(self.payload, self.request)
        for action in ('edit', 'reveal'):
            self.payload['action'] = action; files.open_artifact(self.payload, self.request)
        self.assertEqual(run.call_count, 2)
    @patch.object(files.sys, 'platform', 'darwin')
    @patch.object(files.subprocess, 'run')
    def test_toggle_off_during_resolution_prevents_launch(self, run):
        with self.assertRaises(files.FileActionError): files.open_artifact(self.payload, self.request, lambda: False)
        run.assert_not_called()
    @patch.object(files.sys, 'platform', 'darwin')
    @patch.object(files.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'open'))
    def test_launch_failure_is_user_readable_and_not_retried(self, run):
        with self.assertRaisesRegex(files.FileActionError, '默认应用关联'): files.open_artifact(self.payload, self.request)
        run.assert_called_once()
    def test_missing_credentials_never_contacts_native(self):
        with patch.object(files.http.client, 'HTTPConnection') as connection, self.assertRaises(files.FileActionError):
            files.native_json(0, {}, '/api/artifacts/art_1/metadata')
        connection.assert_not_called()


if __name__ == '__main__': unittest.main()
