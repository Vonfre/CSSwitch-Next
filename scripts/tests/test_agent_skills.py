import base64
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('workspace_agents', Path(__file__).parents[1] / 'science-workspace.py')
w = importlib.util.module_from_spec(spec); spec.loader.exec_module(w)


class AgentLibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'; self.home.mkdir()
        self.work = w.Workspace(self.home, self.home / '.science', self.home / '.csswitch-next')
        self.library = self.work.library

    def tearDown(self): self.temp.cleanup()

    def skill(self, source='agents', name='paper', text='Original'):
        path = self.work.roots[source] / name; path.mkdir(parents=True, exist_ok=True)
        (path / 'SKILL.md').write_text(f'---\nname: {name}\ndescription: Test skill\n---\n{text}')
        (path / 'references').mkdir(exist_ok=True); (path / 'references/ref.md').write_text('Reference')
        return path

    def imported(self):
        source = self.skill(); self.library.import_skill('agents:paper'); return source

    def test_discovery_resolves_known_links_merges_locations_and_preserves_legacy_keys(self):
        source = self.skill(); codex = self.work.roots['codex']; codex.mkdir(parents=True)
        (codex / 'paper').symlink_to(source)
        found = self.work.discover(); self.assertEqual(len(found['skills']), 1)
        self.assertEqual({l['source'] for l in found['skills'][0]['locations']}, {'codex', 'agents'})
        self.assertEqual(next(s for s in found['sources'] if s['id'] == 'codex')['links'], 1)
        first = self.work.skill_bundle('codex:paper'); second = self.work.skill_bundle('agents:paper')
        self.assertEqual(first['fingerprint'], second['fingerprint'])
        self.assertEqual(second['sourceKey'], 'agents:paper')

    def test_external_links_require_registered_directory_and_source_payload_skips_file_links(self):
        outside = self.root / 'extra'; outside.mkdir()
        skill = outside / 'other'; skill.mkdir(); (skill / 'SKILL.md').write_text('---\nname: other\ndescription: Test\n---')
        root = self.work.roots['codex']; root.mkdir(parents=True); (root / 'other').symlink_to(skill)
        self.assertEqual(self.work.discover()['skills'], [])
        self.assertTrue(self.work.discover()['warnings'])
        self.library.source_add({'name': 'External Agent', 'path': str(outside)})
        self.assertEqual(len(self.work.discover()['skills']), 1)
        (skill / 'secret').symlink_to(self.root / 'credential')
        (self.root / 'credential').write_text('private-do-not-copy')
        package = self.work.skill_bundle('codex:other')
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(package['bundle']))) as archive:
            self.assertNotIn('other/secret', archive.namelist())

    def test_import_copy_is_independent_duplicate_idempotent_and_conflict_preserves_library(self):
        source = self.imported(); dest = self.library.root / 'paper'
        self.assertFalse(dest.is_symlink()); self.assertEqual((dest/'references/ref.md').read_text(), 'Reference')
        self.library.import_skill('agents:paper')
        (source/'SKILL.md').write_text('---\nname: paper\ndescription: Changed\n---')
        with self.assertRaises(w.WorkspaceError): self.library.import_skill('agents:paper')
        self.assertIn('Original', (dest/'SKILL.md').read_text())

    def test_symlink_enable_disable_does_not_touch_original_or_library(self):
        source = self.imported()
        self.library.deploy('paper', 'codex', True)
        target = self.work.roots['codex']/'paper'; self.assertTrue(target.is_symlink())
        self.assertEqual(target.resolve(), self.library.root/'paper')
        self.assertEqual(self.library.listing()['skills'][0]['targets']['codex']['status'], 'on')
        self.library.deploy('paper', 'codex', False)
        self.assertFalse(target.exists()); self.assertTrue(source.exists()); self.assertTrue((self.library.root/'paper').exists())

    def test_copy_changes_are_never_overwritten_or_removed(self):
        self.imported(); self.library.deploy('paper', 'claude', True, 'copy')
        target = self.work.roots['claude']/'paper'
        (target/'references/ref.md').write_text('User edits')
        self.assertEqual(self.library.listing()['skills'][0]['targets']['claude']['status'], 'conflict')
        with self.assertRaises(w.WorkspaceError): self.library.deploy('paper', 'claude', False)
        with self.assertRaises(w.WorkspaceError): self.library.deploy('paper', 'claude', True, 'symlink')
        self.assertEqual((target/'references/ref.md').read_text(), 'User edits')

    def test_external_same_name_and_replaced_owned_link_are_protected(self):
        self.imported(); external = self.skill('claude', text='External')
        with self.assertRaises(w.WorkspaceError): self.library.deploy('paper', 'claude', True)
        with self.assertRaises(w.WorkspaceError): self.library.deploy('paper', 'claude', False)
        self.library.deploy('paper', 'codex', True)
        target = self.work.roots['codex']/'paper'; target.unlink(); target.symlink_to(external)
        with self.assertRaises(w.WorkspaceError): self.library.deploy('paper', 'codex', False)
        self.assertTrue(target.is_symlink()); self.assertIn('External', (external/'SKILL.md').read_text())

    def test_auto_falls_back_to_copy_and_explicit_link_failure_leaves_no_receipt(self):
        self.imported()
        with patch.object(Path, 'symlink_to', side_effect=OSError('not supported')):
            self.library.deploy('paper', 'codex', True, 'auto')
            with self.assertRaises(w.WorkspaceError): self.library.deploy('paper', 'claude', True, 'symlink')
        self.assertFalse((self.work.roots['codex']/'paper').is_symlink())
        self.assertNotIn('claude', self.library.index()['skills']['paper']['deployments'])

    def test_update_propagates_links_and_preserves_local_edits(self):
        source = self.imported(); self.library.deploy('paper', 'codex', True)
        expected = self.library.listing()['skills'][0]['currentFingerprint']
        (source/'references/ref.md').write_text('New source')
        self.library.update('paper', expected)
        self.assertEqual((self.work.roots['codex']/'paper/references/ref.md').read_text(), 'New source')
        (self.library.root/'paper/references/ref.md').write_text('Local edit')
        with self.assertRaises(w.WorkspaceError): self.library.update('paper', expected)
        self.assertEqual((self.library.root/'paper/references/ref.md').read_text(), 'Local edit')

    def test_copy_update_requires_resync_and_removal_requires_detach_with_backup(self):
        source = self.imported(); self.library.deploy('paper', 'codex', True, 'copy')
        before = self.library.listing()['skills'][0]['currentFingerprint']
        with self.assertRaises(w.WorkspaceError): self.library.update('paper', before)
        with self.assertRaises(w.WorkspaceError): self.library.remove('paper')
        self.library.deploy('paper', 'codex', False)
        result = self.library.remove('paper')
        self.assertTrue(Path(result['backup']).is_dir()); self.assertTrue(source.is_dir())
        self.assertEqual(self.library.listing()['skills'], [])

    def test_failed_index_writes_roll_back_import_deploy_disable_and_update(self):
        source = self.skill()
        with patch.object(self.library, 'write_json', side_effect=OSError('disk error')):
            with self.assertRaises(OSError): self.library.import_skill('agents:paper')
        self.assertFalse((self.library.root/'paper').exists())
        self.library.import_skill('agents:paper')
        with patch.object(self.library, 'write_json', side_effect=OSError('disk error')):
            with self.assertRaises(OSError): self.library.deploy('paper', 'codex', True)
        self.assertFalse((self.work.roots['codex']/'paper').exists())
        self.library.deploy('paper', 'codex', True)
        expected = self.library.listing()['skills'][0]['currentFingerprint']
        (source/'references/ref.md').write_text('New')
        with patch.object(self.library, 'write_json', side_effect=OSError('disk error')):
            with self.assertRaises(OSError): self.library.deploy('paper', 'codex', False)
            with self.assertRaises(OSError): self.library.update('paper', expected)
        self.assertTrue((self.work.roots['codex']/'paper').is_symlink())
        self.assertEqual((self.library.root/'paper/references/ref.md').read_text(), 'Reference')

    def test_library_exports_science_compatible_bundle_and_new_source_keys(self):
        self.imported(); package = self.work.run('skill_bundle', {'key':'library:paper'})
        name, _, files = w.local_skill_files.unpack(package['filename'], package['bundle'])
        self.assertEqual(name, 'paper'); self.assertIn('references/ref.md', files)
        self.assertEqual(w.local_skill_files.source_key(package['sourceKey']), 'library:paper')
        for prefix in ('windsurf', 'cc-switch', 'pi', 'custom-0123456789abcdef'):
            self.assertEqual(w.local_skill_files.source_key(prefix+':paper'), prefix+':paper')

    def test_linked_manager_roots_are_discovered_and_executable_scripts_survive_copy(self):
        master = self.home/'.another-manager/skills/paper'
        master.mkdir(parents=True)
        (master/'SKILL.md').write_text('---\nname: paper\ndescription: Fixture\n---')
        script = master/'run.sh'; script.write_text('#!/bin/sh\nexit 0\n'); script.chmod(0o700)
        root = self.work.roots['codex']; root.mkdir(parents=True); (root/'paper').symlink_to(master)
        found = self.work.discover()
        self.assertEqual(len(found['skills']), 1)
        self.assertTrue(any(s['id'].startswith('linked-') for s in found['sources']))
        self.library.import_skill('codex:paper')
        self.library.deploy('paper', 'claude', True, 'copy')
        self.assertTrue((self.work.roots['claude']/'paper/run.sh').stat().st_mode & 0o100)

    def test_custom_source_removal_preserves_files_and_guards_active_deployments(self):
        self.imported(); custom = self.home/'custom-skills'; custom.mkdir()
        self.library.source_add({'name':'Custom','path':str(custom)})
        key = next(s['id'] for s in self.library.sources() if s.get('custom'))
        self.library.deploy('paper', key, True)
        with self.assertRaises(w.WorkspaceError): self.library.source_remove(key)
        self.library.deploy('paper', key, False); self.library.source_remove(key)
        self.assertTrue(custom.is_dir())
        self.assertFalse(any(s['id'] == key for s in self.library.sources()))

    def test_agent_names_with_underscores_and_uppercase_round_trip(self):
        self.skill(name='My_Research')
        self.library.import_skill('agents:My_Research')
        self.library.deploy('My_Research', 'codex', True, 'copy')
        self.assertTrue((self.work.roots['codex']/'My_Research/SKILL.md').is_file())
        self.assertEqual(self.library.listing()['skills'][0]['name'], 'My_Research')
        self.library.deploy('My_Research', 'codex', False)

    def test_custom_sources_cannot_overlap_private_library(self):
        self.imported()
        for path in (self.home.parent, self.library.root, self.library.root/'paper', self.work.config):
            with self.assertRaises(w.WorkspaceError):
                self.library.source_add({'name': 'Invalid', 'path': str(path)})

    def test_invalid_names_and_symlink_target_roots_are_rejected(self):
        self.imported()
        for name in ('../escape', '/tmp/escape', '.', None):
            with self.assertRaises(w.WorkspaceError): self.library.deploy(name, 'codex', True)
        outside = self.root/'elsewhere'; outside.mkdir()
        self.work.roots['codex'].parent.mkdir(parents=True, exist_ok=True)
        self.work.roots['codex'].symlink_to(outside)
        with self.assertRaises(w.local_skill_files.LocalSkillError): self.library.deploy('paper', 'codex', True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_direct_toggle_installs_without_manual_import_and_manages_original_directory(self):
        original = self.skill('codex'); original_inode = original.stat().st_ino
        self.library.toggle('paper', 'codex:paper', 'claude', True)
        self.assertTrue((self.work.roots['claude']/'paper').is_symlink())
        self.library.toggle('paper', 'codex:paper', 'codex', False)
        self.assertFalse(original.exists())
        found = self.work.discover()
        self.assertTrue(any(l.get('disabled') and l['source'] == 'codex' for s in found['skills'] for l in s['locations']))
        self.assertEqual(next(s for s in found['sources'] if s['id'] == 'codex')['count'], 0)
        self.assertEqual(next(s for s in found['sources'] if s['id'] == 'codex')['pausedCount'], 1)
        self.library.toggle('paper', 'codex:paper', 'codex', True)
        self.assertEqual(original.stat().st_ino, original_inode)
        self.assertTrue((self.work.roots['claude']/'paper/SKILL.md').is_file())

    def test_paused_relative_link_preserves_resolution_and_can_be_previewed(self):
        master = self.skill('agents'); target = self.work.roots['codex']; target.mkdir(parents=True)
        (target/'paper-alias').symlink_to(os.path.relpath(master, target))
        link_text = os.readlink(target/'paper-alias')
        self.library.toggle('paper', 'codex:paper-alias', 'codex', False)
        receipt = next(iter(self.library.index()['paused'].values()))
        self.assertEqual((target/receipt['backup']).resolve(), master)
        self.assertIn('Original', self.work.run('skill_library_preview', {'key':'codex:paper-alias'})['content'])
        self.library.toggle('paper', 'codex:paper-alias', 'codex', True)
        self.assertEqual(os.readlink(target/'paper-alias'), link_text)
        self.assertTrue(master.is_dir())

    def test_pause_restore_transactions_and_restore_conflicts_preserve_originals(self):
        original = self.skill('codex')
        with patch.object(self.library, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.library.toggle('paper','codex:paper','codex',False)
        self.assertTrue(original.is_dir()); self.assertFalse(self.library.index().get('paused'))
        self.library.toggle('paper','codex:paper','codex',False)
        receipt = next(iter(self.library.index()['paused'].values())); backup = original.parent/receipt['backup']
        with patch.object(self.library, 'write_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.library.toggle('paper','codex:paper','codex',True)
        self.assertTrue(backup.is_dir()); self.assertFalse(original.exists())
        original.mkdir(); (original/'user-file').write_text('new original')
        with self.assertRaises(w.WorkspaceError): self.library.toggle('paper','codex:paper','codex',True)
        self.assertEqual((original/'user-file').read_text(), 'new original'); self.assertTrue(backup.is_dir())

    def test_paused_only_source_stays_discoverable_and_cannot_be_unregistered(self):
        custom = self.home/'custom'; custom.mkdir(); self.library.source_add({'name':'Custom','path':str(custom)})
        target = next(s['id'] for s in self.library.sources() if s.get('custom'))
        original = custom/'paper'; original.mkdir(); (original/'SKILL.md').write_text('---\nname: paper\ndescription: Only source\n---')
        self.library.toggle('paper',target+':paper',target,False)
        found = self.work.discover()['skills']; self.assertEqual(len(found),1)
        self.assertTrue(found[0]['locations'][0]['disabled'])
        with self.assertRaises(w.WorkspaceError): self.library.source_remove(target)
        self.library.toggle('paper',target+':paper','codex',True)
        self.assertTrue((self.work.roots['codex']/'paper/SKILL.md').is_file())
        self.library.toggle('paper',target+':paper',target,True)
        self.assertTrue(original.exists())

    def test_direct_toggle_preserves_other_agents_linking_to_original(self):
        original = self.skill('codex'); target = self.work.roots['claude']; target.mkdir(parents=True)
        (target/'paper').symlink_to(original)
        with self.assertRaises(w.WorkspaceError): self.library.toggle('paper','codex:paper','codex',False)
        self.assertTrue((target/'paper/SKILL.md').is_file())
        self.library.toggle('paper','codex:paper','claude',False)
        self.assertTrue(original.is_dir()); self.assertFalse((target/'paper').exists())

    def test_new_same_name_directory_cannot_replace_an_existing_paused_backup(self):
        original = self.skill('codex')
        self.library.toggle('paper','codex:paper','codex',False)
        replacement = self.skill('codex', text='Replacement')
        with self.assertRaises(w.WorkspaceError): self.library.toggle('paper','codex:paper','codex',False)
        self.assertIn('Replacement',(replacement/'SKILL.md').read_text())
        self.assertEqual(len(self.library.index()['paused']),1)

    def test_changed_paused_link_is_not_restored(self):
        original = self.skill('agents'); target = self.work.roots['codex']; target.mkdir(parents=True)
        (target/'paper').symlink_to(original)
        self.library.toggle('paper','codex:paper','codex',False)
        receipt = next(iter(self.library.index()['paused'].values()))
        backup = target/receipt['backup']; backup.unlink(); backup.symlink_to(self.home/'elsewhere')
        with self.assertRaises(w.WorkspaceError): self.library.toggle('paper','codex:paper','codex',True)
        self.assertTrue(backup.is_symlink()); self.assertFalse((target/'paper').exists())

    def test_stale_name_does_not_import_an_unrequested_skill(self):
        self.skill('agents')
        with self.assertRaises(w.WorkspaceError): self.library.toggle('different','agents:paper','codex',True)
        self.assertFalse(self.library.index()['skills'])

if __name__ == '__main__': unittest.main()
