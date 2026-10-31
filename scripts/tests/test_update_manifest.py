import base64
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('manifest', ROOT / 'scripts/generate-update-manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.assets = Path(self.temp.name)
        for arch in ('arm64', 'x64'):
            for suffix in ('dmg', 'app.zip', 'app.tar.gz'):
                (self.assets / f'CSSwitch-Next-0.5.0-{arch}.{suffix}').write_bytes(b'fixture')
            (self.assets / f'CSSwitch-Next-0.5.0-{arch}.app.tar.gz.sig').write_text(base64.b64encode(b'untrusted comment: fixture\ntrusted comment: timestamp:0\tversion:0.5.0\n').decode())

        for suffix in ('-setup.exe', '.zip'):
            (self.assets / f'CSSwitch-Next-0.5.0-windows-x64{suffix}').write_bytes(b'fixture')
        (self.assets / 'CSSwitch-Next-0.5.0-windows-x64-setup.exe.sig').write_text(base64.b64encode(b'untrusted comment: fixture\ntrusted comment: timestamp:0\tversion:0.5.0\n').decode())

    def test_architecture_mapping_and_release_urls(self):
        data = manifest.generate(ROOT, self.assets, '0.5.0', 'Vonfre/CSSwitch-Next', verify=False)
        self.assertEqual(set(data['platforms']), {'darwin-aarch64', 'darwin-x86_64', 'windows-x86_64'})
        self.assertTrue(data['platforms']['darwin-aarch64']['url'].endswith('/v0.5.0/CSSwitch-Next-0.5.0-arm64.app.tar.gz'))
        self.assertTrue(data['platforms']['windows-x86_64']['url'].endswith('/v0.5.0/CSSwitch-Next-0.5.0-windows-x64-setup.exe'))
        self.assertIn('应用内更新', data['notes'])
        json.loads(json.dumps(data))

    def test_incomplete_or_malformed_assets_cannot_publish(self):
        (self.assets / 'CSSwitch-Next-0.5.0-x64.app.tar.gz').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing updater archive'):
            manifest.generate(ROOT, self.assets, '0.5.0', 'Vonfre/CSSwitch-Next', verify=False)

    def test_invalid_signature_and_version_are_rejected(self):
        (self.assets / 'CSSwitch-Next-0.5.0-arm64.app.tar.gz.sig').write_text('not a signature!')
        with self.assertRaises(ValueError):
            manifest.generate(ROOT, self.assets, '0.5.0', 'Vonfre/CSSwitch-Next', verify=False)
        with self.assertRaises(ValueError):
            manifest.generate(ROOT, self.assets, '../unsafe', 'Vonfre/CSSwitch-Next', verify=False)

    def test_windows_portable_and_version_bound_signature_are_required(self):
        portable = self.assets / 'CSSwitch-Next-0.5.0-windows-x64.zip'
        portable.unlink()
        with self.assertRaisesRegex(ValueError, 'Missing installer'):
            manifest.generate(ROOT, self.assets, '0.5.0', 'Vonfre/CSSwitch-Next', verify=False)
        portable.write_bytes(b'fixture')
        (self.assets / 'CSSwitch-Next-0.5.0-windows-x64-setup.exe.sig').write_text(base64.b64encode(b'untrusted comment: fixture\ntrusted comment: timestamp:0\tversion:0.4.0\n').decode())
        with self.assertRaisesRegex(ValueError, 'not bound to release version'):
            manifest.generate(ROOT, self.assets, '0.5.0', 'Vonfre/CSSwitch-Next', verify=False)
