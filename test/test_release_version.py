import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys

if sys.version_info < (3, 11):
    raise unittest.SkipTest("Release validation uses Python 3.11+ on CI")

spec = importlib.util.spec_from_file_location("release_version", Path(__file__).resolve().parents[1] / "scripts/check-release-version.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class ReleaseVersion(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.desktop = self.root / "desktop"
        (self.desktop / "src-tauri").mkdir(parents=True)
        self.write_json("package.json", {"version": "0.4.1"})
        self.write_json("package-lock.json", {"version": "0.4.1", "packages": {"": {"version": "0.4.1"}}})
        self.write_json("src-tauri/tauri.conf.json", {"version": "0.4.1"})
        (self.desktop / "src-tauri/Cargo.toml").write_text('[package]\nname = "desktop"\nversion = "0.4.1"\n')
        (self.desktop / "src-tauri/Cargo.lock").write_text('[[package]]\nname = "desktop"\nversion = "0.4.1"\n')

    def write_json(self, path, data):
        (self.desktop / path).write_text(json.dumps(data))

    def test_matching_tag_and_branch(self):
        self.assertEqual(release.check_version(self.root, "v0.4.1"), "0.4.1")
        self.assertEqual(release.check_version(self.root), "0.4.1")

    def test_rejects_wrong_or_unsafe_tag(self):
        for tag in ("v0.4.2", "0.4.1", "main", "v0.4.1; command"):
            with self.assertRaises(ValueError):
                release.check_version(self.root, tag)

    def test_rejects_mismatched_json_versions(self):
        for path in ("package.json", "src-tauri/tauri.conf.json"):
            self.write_json(path, {"version": "0.4.2"})
            with self.assertRaises(ValueError):
                release.check_version(self.root)
            self.write_json(path, {"version": "0.4.1"})

    def test_rejects_stale_npm_lock(self):
        for top, nested in (("0.4.2", "0.4.1"), ("0.4.1", "0.4.2")):
            self.write_json("package-lock.json", {"version": top, "packages": {"": {"version": nested}}})
            with self.assertRaises(ValueError):
                release.check_version(self.root)

    def test_rejects_stale_cargo_lock(self):
        (self.desktop / "src-tauri/Cargo.lock").write_text('[[package]]\nname = "desktop"\nversion = "0.4.2"\n')
        with self.assertRaises(ValueError):
            release.check_version(self.root)
