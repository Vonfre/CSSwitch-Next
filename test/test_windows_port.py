"""Windows-specific regressions, including NTFS privacy and single-DB history."""
import importlib.util
import io
import base64
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'proxy'))
from private_files import protect_windows, windows_private

class WindowsPort(unittest.TestCase):
    def test_portable_archives_reject_windows_path_escapes(self):
        spec=importlib.util.spec_from_file_location('local_skill_port',ROOT/'scripts/science-local-skills.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        for filename in ('C:/outside.txt','paper/C:escape','paper/file:stream','paper/CON.txt','paper/file.','paper/file '):
            with self.subTest(filename=filename):
                out=io.BytesIO()
                with zipfile.ZipFile(out,'w') as z:
                    z.writestr('paper/SKILL.md','---\nname: paper\ndescription: test\n---\n')
                    z.writestr(filename,'inert')
                with self.assertRaises(module.LocalSkillError): module.unpack('paper.zip',base64.b64encode(out.getvalue()).decode())

    @unittest.skipUnless(os.name=='nt','NTFS DACL')
    def test_private_config_dacl_is_readable_and_rejects_public_grant(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'private.json';path.write_text('{}')
            protect_windows(path)
            with path.open('rb') as f: self.assertTrue(windows_private(f.fileno()))
            import subprocess
            subprocess.run(['icacls.exe',str(path),'/grant','*S-1-1-0:R'],check=True,capture_output=True)
            with path.open('rb') as f: self.assertFalse(windows_private(f.fileno()))

    def test_single_database_history_and_transcript(self):
        spec=importlib.util.spec_from_file_location('workspace_port',ROOT/'scripts/science-workspace.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);data=root/'data';data.mkdir();db=sqlite3.connect(data/'operon-cli.db')
            try:
                db.executescript('CREATE TABLE projects(id TEXT,name TEXT); CREATE TABLE frames(id TEXT,project_id TEXT,parent_frame_id TEXT,created_at INTEGER,updated_at INTEGER); CREATE TABLE frame_messages(frame_id TEXT,idx INTEGER,msg_json TEXT);')
                db.execute('INSERT INTO projects VALUES (?,?)',('p','中文项目'))
                db.execute('INSERT INTO frames VALUES (?,?,?,?,?)',('f','p',None,1,2))
                db.execute('INSERT INTO frame_messages VALUES (?,?,?)',('f',0,json.dumps({'role':'user','content':'中文消息'})))
                db.commit()
            finally: db.close()
            work=module.Workspace(root,data,root/'config')
            result=work.sessions();self.assertFalse(result['warnings'])
            self.assertEqual(result['sessions'][0]['key'],'local:f')
            self.assertEqual(result['sessions'][0]['project_name'],'中文项目')
            self.assertTrue(work.transcript('local:f'))
            # Windows can rename only after all SQLite handles have closed.
            (data/'operon-cli.db').rename(data/'closed.db')

if __name__=='__main__': unittest.main()
