import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('compat', Path(__file__).parents[1] / 'science-compatibility.py')
compat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compat)


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.binary = self.home / 'claude-science'
        self.binary.touch(); self.binary.chmod(0o700)
        self.bundle = self.home / 'Info.plist'
        self.bundle.write_bytes(plistlib.dumps({'CFBundleShortVersionString': '0.1.25'}))
        self.runtime = self.home / '.claude-science/runtime/0.1.67-release'
        for name in ['.ok', 'mcp-servers/bio-tools/run_server.py', 'mcp-servers/ketcher-chemistry/server.js', 'mcp-servers/ketcher-chemistry/widget/index.html']:
            p = self.runtime / name; p.parent.mkdir(parents=True, exist_ok=True); p.touch()
        profiles_spec = importlib.util.spec_from_file_location('test_profiles', Path(__file__).parents[1] / 'science-connectors.py')
        profiles = importlib.util.module_from_spec(profiles_spec); profiles_spec.loader.exec_module(profiles)
        for key in profiles.NAMES:
            p = self.runtime / f'mcp-servers/bio-tools/lib/mcp_{key.replace("-", "_")}/server.py'
            p.parent.mkdir(parents=True, exist_ok=True); p.touch()
        p = self.home / '.claude-science/conda/envs/claude-science-mcp/bin/python'
        p.parent.mkdir(parents=True); p.touch()

    def runner(self, command):
        if command[-1] == '--help': return '\n'.join(compat.FLAGS)
        return 'claude-science 0.1.67 (release, public)' if command[0] == str(self.binary) else 'v22.12.0'

    def inspect(self, runner=None):
        return compat.inspect(self.home, self.binary, '/usr/bin/true', self.bundle, runner or self.runner)

    def test_actual_version_takes_precedence_and_inspection_does_not_write(self):
        before = {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        result = self.inspect()
        self.assertEqual(result['science_version'], '0.1.67')
        self.assertEqual(result['bundle_version'], '0.1.25')
        self.assertEqual(result['available_profiles'], 24)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(before, {p: p.read_bytes() for p in self.home.rglob('*') if p.is_file()})

    def test_incomplete_update_ignored_but_complete_mismatch_reported(self):
        newer = self.runtime.parent / '0.1.68-release'; newer.mkdir()
        self.assertEqual(self.inspect()['status'], 'pass')
        (newer / '.ok').touch()
        result = self.inspect()
        self.assertEqual(result['runtime_version'], '0.1.68')
        self.assertEqual(result['status'], 'warning')
        self.assertEqual(result['available_profiles'], 0)

    def test_missing_binary_and_runtime_are_actionable(self):
        self.binary.unlink(); (self.runtime / '.ok').unlink()
        result = self.inspect()
        self.assertEqual(result['status'], 'error')
        self.assertIsNone(result['science_version'])
        self.assertTrue(all(c['action'] for c in result['checks'] if c['status'] == 'error'))

    def test_unknown_version_is_not_claimed_compatible(self):
        result = self.inspect(lambda command: self.runner(command).replace('0.1.67', '0.1.99'))
        self.assertEqual(result['status'], 'warning')
        self.assertEqual(next(c for c in result['checks'] if c['id'] == 'baseline')['status'], 'warning')

    def test_failed_probe_output_is_not_exported(self):
        result = self.inspect(lambda command: 'private-token-value /Users/private' if command[-1] == '--help' else self.runner(command))
        self.assertEqual(result['status'], 'warning')
        self.assertNotIn('private-token-value', json.dumps(result))
        self.assertNotIn('/Users/private', json.dumps(result))

    def test_timeout_and_command_failure_return_no_raw_output(self):
        with patch.object(compat.subprocess, 'run', side_effect=subprocess.TimeoutExpired('probe', 5)):
            self.assertEqual(compat.output(['probe']), '')
        with patch.object(compat.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, 'secret', 'secret')):
            self.assertEqual(compat.output(['probe']), '')

    def test_missing_node_only_affects_ketcher_dependency(self):
        with patch('shutil.which', return_value=None):
            result = compat.inspect(self.home, self.binary, None, self.bundle, self.runner)
        self.assertEqual(result['available_profiles'], 23)
        self.assertEqual(result['status'], 'warning')


if __name__ == '__main__':
    unittest.main()
