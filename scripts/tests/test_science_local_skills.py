import base64
from contextlib import closing
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('local_skills', Path(__file__).parents[1]/'science-local-skills.py')
local = importlib.util.module_from_spec(spec); spec.loader.exec_module(local)


class LocalSkillsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.data, self.auth = self.root/'data', self.root/'auth'
        self.auth.mkdir()
        (self.auth/'active-org.json').write_text(json.dumps({'org_uuid':'12345678-1234-1234-1234-123456789abc'}))
        org = self.data/'orgs/12345678-1234-1234-1234-123456789abc'
        org.mkdir(parents=True)
        with closing(sqlite3.connect(org/'operon-cli.db')) as db, db:
            db.execute('CREATE TABLE capability_settings(user_id TEXT, kind TEXT, key TEXT, enabled INTEGER)')
            db.execute('INSERT INTO capability_settings VALUES (?,?,?,?)', ('user','skill','local-fixture',0))
        self.store = local.LocalSkills(str(self.data),str(self.auth))
        self.text = b'---\nname: local-fixture\ndescription: Local fixture skill\n---\n# Fixture\n'
        self.original_db = self.store.dbpath.read_bytes()

    def tearDown(self):
        self.assertEqual(self.store.dbpath.read_bytes(), self.original_db)
        self.temp.cleanup()

    def zip(self, files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,content in files: archive.writestr(name,content)
        return base64.b64encode(stream.getvalue()).decode()

    def test_zip_preserves_references_and_installs_owned_native_draft(self):
        encoded = self.zip([('fixture/SKILL.md', self.text),('fixture/references/test.md',b'Reference'),('fixture/scripts/run.py',b'# never executed')])
        name,description,files = local.unpack('fixture.skill', encoded)
        self.assertEqual(name,'local-fixture')
        self.assertEqual(description,'Local fixture skill')
        self.store.install(name,files,set())
        self.assertEqual((self.store.root/name/'references/test.md').read_bytes(),b'Reference')
        self.assertEqual(self.store.info(name)['source'],'csswitch-local')
        self.assertFalse((self.store.root/name/'.catalog_stamp').exists())
        self.assertEqual(self.store.disabled('user'),{'local-fixture'})
        self.store.remove(name)
        self.assertFalse((self.store.root/name).exists())

    def test_md_name_comes_from_frontmatter_not_upload_filename(self):
        name,_,files = local.unpack('科研笔记.md',base64.b64encode(self.text).decode())
        self.assertEqual(name,'local-fixture')
        self.assertEqual(files,{'SKILL.md':self.text})

    def test_update_replaces_support_files_and_restores_every_file_on_failure(self):
        original = {'SKILL.md': self.text, 'references/old.md': b'old'}
        self.store.install('local-fixture', original, set(), 'codex:local-fixture')
        before = self.store.info('local-fixture')
        changed = {'SKILL.md': self.text + b'\nUpdated', 'scripts/new.py': b'# inert'}
        with self.assertRaisesRegex(RuntimeError, 'verification'):
            with self.store.replacing('local-fixture', changed, before['fingerprint'], 'codex:local-fixture'):
                self.assertEqual(self.store.files(self.store.root/'local-fixture'), changed)
                raise RuntimeError('verification failed')
        self.assertEqual(self.store.files(self.store.root/'local-fixture'), original)
        with self.store.replacing('local-fixture', changed, before['fingerprint'], 'codex:local-fixture'): pass
        self.assertEqual(self.store.files(self.store.root/'local-fixture'), changed)
        self.assertFalse(any(p.name.startswith(('.csswitch-stage-', '.csswitch-backup-')) for p in self.store.root.iterdir()))

    def test_update_rejects_edits_stale_hash_wrong_source_and_name(self):
        self.store.install('local-fixture', {'SKILL.md':self.text}, set(), 'codex:local-fixture')
        before = self.store.info('local-fixture')
        for expected, origin, content in [('stale', 'codex:local-fixture',self.text), (before['fingerprint'],'agents:local-fixture',self.text), (before['fingerprint'],'codex:local-fixture',self.text.replace(b'local-fixture',b'other'))]:
            with self.assertRaises(local.LocalSkillError):
                with self.store.replacing('local-fixture', {'SKILL.md':content}, expected, origin): pass
        (self.store.root/'local-fixture/SKILL.md').write_bytes(self.text+b'\nUser edit')
        edited = self.store.info('local-fixture')
        self.assertTrue(edited['locallyModified'])
        with self.assertRaisesRegex(local.LocalSkillError,'本地修改'):
            with self.store.replacing('local-fixture', {'SKILL.md':self.text}, edited['fingerprint'], 'codex:local-fixture'): pass

    def test_finder_zip_metadata_is_ignored_and_case_collisions_rejected(self):
        encoded=self.zip([('fixture/SKILL.md',self.text),('fixture/.DS_Store',b'Finder'),('__MACOSX/fixture/._SKILL.md',b'Resource fork')])
        self.assertEqual(local.unpack('fixture.zip',encoded)[2],{'SKILL.md':self.text})
        encoded=self.zip([('fixture/SKILL.md',self.text),('fixture/references/Test.md',b'first'),('fixture/references/test.md',b'second')])
        with self.assertRaises(local.LocalSkillError): local.unpack('fixture.zip',encoded)

    def test_existing_published_skills_and_aliases_are_not_overwritten(self):
        self.store.root.mkdir()
        existing=self.store.root/'local-fixture';existing.mkdir()
        (existing/'.catalog_stamp').write_text('official')
        (existing/'SKILL.md').write_bytes(self.text)
        with self.assertRaises(local.LocalSkillError): self.store.install('local-fixture',{'SKILL.md':b'changed'},set())
        with self.assertRaises(local.LocalSkillError): self.store.remove('local-fixture')
        self.assertEqual((existing/'SKILL.md').read_bytes(),self.text)
        with self.assertRaises(local.LocalSkillError): self.store.install('customize',{'SKILL.md':self.text},{'operon-customize'})

    def test_managed_skill_becoming_published_cannot_be_removed(self):
        self.store.install('local-fixture',{'SKILL.md':self.text},set())
        (self.store.root/'local-fixture/.catalog_stamp').write_text('published')
        self.assertIsNone(self.store.info('local-fixture'))
        with self.assertRaises(local.LocalSkillError): self.store.remove('local-fixture')

    def test_archive_paths_symlinks_special_files_and_sidecars_rejected(self):
        entries=[('../escape',0),('/absolute',0),('fixture/../../escape',0),('fixture\\escape',0),('fixture/link',0o120777),('fixture/device',0o020600),('fixture/.catalog_stamp',0),('fixture/.csswitch-local.json',0)]
        for filename,mode in entries:
            with self.subTest(filename=filename):
                item=zipfile.ZipInfo(filename);item.external_attr=mode<<16
                # ZipInfo normalizes backslashes on Windows; retain the actual
                # malformed archive entry we want the importer to reject.
                item.filename = filename
                encoded=self.zip([('fixture/SKILL.md',self.text),(item,b'unsafe')])
                with self.assertRaises(local.LocalSkillError): local.unpack('fixture.zip',encoded)

    @patch.object(local, 'MAX_BYTES', 1024)
    def test_multi_skill_broken_duplicate_and_oversized_archives_rejected(self):
        cases=[self.zip([('a/SKILL.md',self.text),('b/SKILL.md',self.text)]),self.zip([('fixture/README.md',b'no skill')]),base64.b64encode(b'not a zip').decode(),self.zip([('fixture/SKILL.md',self.text),('fixture/huge',b'x'*(local.MAX_BYTES+1))])]
        for encoded in cases:
            with self.assertRaises(local.LocalSkillError): local.unpack('fixture.zip',encoded)

    @patch.object(local, 'MAX_BYTES', 1024)
    def test_invalid_metadata_and_encoded_file_limits_rejected(self):
        texts=[b'plain markdown',self.text.replace(b'name: local-fixture',b'name: ../escape'),self.text.replace(b'name: local-fixture',b'name: bad_name'),self.text.replace(b'description: Local fixture skill',b'description:'),self.text.replace(b'name: local-fixture',b'name: first\nname: second'),b'\xff']
        for text in texts:
            with self.assertRaises(local.LocalSkillError): local.unpack('test.md',base64.b64encode(text).decode())
        for encoded in ['', 'invalid', base64.b64encode(b'x'*(local.MAX_BYTES+1)).decode()]:
            with self.assertRaises(local.LocalSkillError): local.unpack('test.md',encoded)

    def test_symlinked_roots_identity_and_skill_dirs_rejected(self):
        outside=self.root/'outside';outside.mkdir()
        try:
            self.store.root.symlink_to(outside)
        except OSError as error:
            if getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows symbolic link privilege is unavailable')
            raise
        with self.assertRaises(local.LocalSkillError): local.LocalSkills(str(self.data),str(self.auth))
        self.store.root.unlink();self.store.root.mkdir()
        (self.store.root/'local-fixture').symlink_to(outside)
        with self.assertRaises(local.LocalSkillError): self.store.install('local-fixture',{'SKILL.md':self.text},set())
        self.assertEqual(list(outside.iterdir()),[])
        identity=self.auth/'active-org.json';identity.unlink();identity.symlink_to(self.root/'missing')
        with self.assertRaises(local.LocalSkillError): local.LocalSkills(str(self.data),str(self.auth))


if __name__=='__main__': unittest.main()
