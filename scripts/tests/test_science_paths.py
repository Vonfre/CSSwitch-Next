"""Directory discovery never creates, moves, or guesses between histories."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from science_paths import resolve, expand


class SciencePathsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='science-目录 ')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.native = self.home / '.claude-science'
        self.env = {'USERPROFILE': str(self.home), 'HOME': str(self.home / 'wrong-home')}

    def resolve(self):
        return resolve(env=self.env, windows=True)

    def history(self, root, direct=False):
        db = root / ('operon-cli.db' if direct else 'orgs/org-a/operon-cli.db')
        db.parent.mkdir(parents=True, exist_ok=True)
        db.touch()

    def config(self, text):
        self.native.mkdir(exist_ok=True)
        (self.native / 'config.toml').write_text(text, encoding='utf-8-sig')

    def test_fresh_install_uses_userprofile_without_creating_data(self):
        value = self.resolve()
        self.assertEqual(Path(value['data']), self.native)
        self.assertFalse(value['history_found'])
        self.assertFalse(self.native.exists())

    def test_native_org_history_and_binary(self):
        self.history(self.native)
        binary = self.home / 'AppData/Local/Programs/ClaudeScience/claude-science.exe'
        binary.parent.mkdir(parents=True)
        binary.touch()
        value = self.resolve()
        self.assertTrue(value['history_found'])
        self.assertEqual(Path(value['binary']), binary)

    def test_appdata_history_fallback_ignores_empty_cache(self):
        root = self.home / 'AppData/Roaming/Claude Science'
        self.history(root)
        self.native.mkdir()
        (root / 'encryption.key').touch()
        value = self.resolve()
        self.assertEqual(Path(value['data']), root)
        self.assertEqual(Path(value['auth']), root)

    def test_multiple_histories_require_explicit_selection(self):
        root = self.home / 'AppData/Local/ClaudeScience'
        self.history(self.native)
        self.history(root, direct=True)
        with self.assertRaisesRegex(ValueError, '多个 Science 对话目录'):
            self.resolve()
        self.env['CSSWITCH_SCIENCE_DATA_DIR'] = str(root)
        self.assertEqual(Path(self.resolve()['data']), root)

    def test_config_bom_windows_tilde_and_separate_auth(self):
        self.config("data_dir = '~\\研究 数据'\n[paths]\nauth_dir = '~\\认证'\n")
        value = self.resolve()
        self.assertEqual(Path(value['data']), self.home / '研究 数据')
        self.assertEqual(Path(value['auth']), self.home / '认证')

    def test_environment_paths_use_injected_environment(self):
        self.env['SCIENCE_DATA'] = str(self.home / '空间')
        self.env['CSSWITCH_SCIENCE_DATA_DIR'] = '%SCIENCE_DATA%'
        self.assertEqual(Path(self.resolve()['data']), self.home / '空间')

    def test_explicit_config_missing_does_not_silently_use_default(self):
        self.env['CSSWITCH_SCIENCE_CONFIG'] = str(self.home / 'missing.toml')
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.resolve()

    def test_config_is_preserved_and_relative_paths_rejected(self):
        self.config("data_dir = 'relative-data'\n")
        before = (self.native / 'config.toml').read_bytes()
        with self.assertRaisesRegex(ValueError, '绝对路径'):
            self.resolve()
        self.assertEqual((self.native / 'config.toml').read_bytes(), before)

    def test_migration_blocks_discovery(self):
        self.config("data_dir_from = '~/old'\n")
        with self.assertRaisesRegex(ValueError, '迁移'):
            self.resolve()

    def test_appdata_config_fallback_and_explicit_priority(self):
        root = self.home / 'AppData/Local/ClaudeScience'
        root.mkdir(parents=True)
        config = root / 'config.toml'
        config.write_text("data_dir = '~/configured'", encoding='utf-8')
        self.assertEqual(Path(self.resolve()['data']), self.home / 'configured')
        self.env['CSSWITCH_SCIENCE_CONFIG'] = str(config)
        self.env['CSSWITCH_SCIENCE_DATA_DIR'] = str(self.home / 'override')
        self.assertEqual(Path(self.resolve()['data']), self.home / 'override')


if __name__ == '__main__':
    unittest.main()
