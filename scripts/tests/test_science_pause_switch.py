import http.cookiejar
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from science_pause_switch import switch, pause_running
from science_hot_switch import configure, stop_gateway
from subscription_store import Store, SubscriptionError


class PauseSwitchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name).resolve()
        self.data = self.home / '.claude-science'
        self.org = '20000000-0000-4000-8000-000000000001'
        self.frame = '30000000-0000-4000-8000-000000000001'
        self.other = '30000000-0000-4000-8000-000000000002'
        self.dbpath = self.data / 'orgs' / self.org / 'operon-cli.db'
        self.dbpath.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.executescript('CREATE TABLE frames(id TEXT PRIMARY KEY,root_frame_id TEXT,status TEXT); CREATE TABLE frame_messages(frame_id TEXT,idx INTEGER,msg_json TEXT);')
            db.execute('INSERT INTO frames VALUES (?,?,?)', (self.frame,self.frame,'cancelled'))
            db.execute('INSERT INTO frames VALUES (?,?,?)', (self.other,self.other,'failed'))
            db.execute('INSERT INTO frame_messages VALUES (?,0,?)', (self.frame,'saved partial reply and tool result'))
        self.store = Store(self.home / '.csswitch-next/subscriptions')
        self.ids = [self.store.add({'account_uuid': name, 'org_uuid': self.org, 'email': name+'@example.test',
                                   'access_token': name,'expires_at':time.time()+3600}) for name in ('A','B')]
        with self.store.locked():
            state=self.store.read();state.update(native_mode=True,hot_mode=True,mode='auto');self.store.write(state)
        self.running = True
        self.manager = SimpleNamespace(store=self.store,science='fixture-science',
            science_state=lambda: {'running':self.running,'pid':123,'started_at':'same'},
            owned=Mock(return_value=True),health=Mock(return_value={'active_requests':1}),
            run=Mock(return_value=SimpleNamespace(stdout='claude-science 0.1.72')),
            route=Mock(),ensure_gateway=Mock(),gateway_request=Mock())
        self.native = Mock(data=self.data, journal=self.home/'switch.json')
        self.native.active.return_value = {'account_uuid':'A','org_uuid':self.org}
        self.native.json_file.return_value = {}
        self.native_patch = patch('science_native_switch.NativeSwitcher', return_value=self.native)
        self.native_patch.start();self.addCleanup(self.native_patch.stop)

    def statuses(self):
        with closing(sqlite3.connect(self.dbpath)) as db:
            return dict(db.execute('SELECT id,status FROM frames'))

    def processing(self, both=False):
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            if both: db.execute("UPDATE frames SET status='processing'")
            else: db.execute("UPDATE frames SET status='processing' WHERE id=?", (self.frame,))

    def stop_api(self, valid=True, persist=True):
        cookie = SimpleNamespace(name='operon_csrf',value='fixture-csrf')
        jar = http.cookiejar.CookieJar()
        handler = __import__('urllib.request',fromlist=['HTTPCookieProcessor']).HTTPCookieProcessor(jar)
        handler.cookiejar = [cookie]
        opener=Mock(handlers=[handler])
        def request(req, **_):
            self.assertEqual(req.get_header('Origin'),'http://127.0.0.1:8000')
            if req.full_url.endswith('/api/csrf'): return io.BytesIO(b'{}')
            self.assertIn('/cancel?',req.full_url)
            self.assertEqual(req.get_header('X-operon-csrf'),'fixture-csrf')
            if persist:
                with closing(sqlite3.connect(self.dbpath)) as db, db:
                    db.execute("UPDATE frames SET status='cancelled' WHERE id=?",(self.frame,))
            return io.BytesIO(json.dumps({'root_frame_id':self.frame if valid else self.other}).encode())
        opener.open.side_effect=request
        self.native.local_client.return_value=(opener,'http://127.0.0.1:8000')
        return opener

    def test_already_paused_stays_paused_and_does_not_wait_for_gateway_stream(self):
        result=switch(self.manager,self.ids[1])
        self.assertTrue(result['same_process'])
        self.assertTrue(result['resume_required'])
        self.assertEqual(result['paused_conversations'],0)
        self.assertEqual(self.statuses(),{self.frame:'cancelled',self.other:'failed'})
        self.native.local_client.assert_not_called()
        self.native.launch.assert_not_called()
        self.manager.route.assert_called_once_with(self.ids[1],prefer=True)

    def test_live_reply_uses_native_stop_and_keeps_saved_transcript(self):
        self.processing();opener=self.stop_api()
        result=switch(self.manager,self.ids[1])
        self.assertEqual(result['paused_conversations'],1)
        self.assertEqual(self.statuses()[self.frame],'cancelled')
        self.assertEqual(self.statuses()[self.other],'failed')
        with closing(sqlite3.connect(self.dbpath)) as db:
            self.assertEqual(db.execute('SELECT msg_json FROM frame_messages').fetchone()[0],'saved partial reply and tool result')
        self.assertEqual(opener.open.call_count,2)
        self.native.launch.assert_not_called()

    def test_multiple_running_conversations_are_not_stopped_indiscriminately(self):
        self.processing(both=True)
        with self.assertRaisesRegex(SubscriptionError,'多个会话'):
            switch(self.manager,self.ids[1])
        self.native.local_client.assert_not_called();self.manager.route.assert_not_called()

    def test_cooldown_target_is_rejected_before_stopping(self):
        self.processing()
        with self.store.locked():
            state=self.store.read();state['accounts'][self.ids[1]]['cooldown_until']=time.time()+300;self.store.write(state)
        with self.assertRaisesRegex(SubscriptionError,'额度冷却'):
            switch(self.manager,self.ids[1])
        self.native.local_client.assert_not_called();self.manager.route.assert_not_called()

    def test_unknown_version_and_pending_transaction_do_not_stop(self):
        self.manager.run.return_value.stdout='claude-science 0.1.73'
        with self.assertRaisesRegex(SubscriptionError,'版本'):
            switch(self.manager,self.ids[1])
        self.native.json_file.return_value={'phase':'prepared'}
        with self.assertRaisesRegex(SubscriptionError,'恢复'):
            switch(self.manager,self.ids[1])
        self.native.local_client.assert_not_called()

    def test_unconfirmed_or_unpersisted_stop_never_changes_account(self):
        self.processing();self.stop_api(valid=False,persist=False)
        with self.assertRaisesRegex(SubscriptionError,'尚未确认'):
            switch(self.manager,self.ids[1])
        self.stop_api(persist=False)
        with patch('science_pause_switch.time.monotonic',side_effect=[0,21]):
            with self.assertRaisesRegex(SubscriptionError,'保存暂停状态'):
                switch(self.manager,self.ids[1])
        self.manager.route.assert_not_called()

    def test_disconnected_handoff_pauses_before_reconnecting(self):
        self.processing();self.stop_api();self.manager.owned.return_value=False
        def reconnect(manager, **kwargs):
            self.assertEqual(self.statuses()[self.frame],'cancelled')
            self.assertEqual(kwargs,{'enabled':True,'pause_active':True})
            return {'same_process':False}
        with patch('science_hot_switch.configure',side_effect=reconnect):
            result=switch(self.manager,self.ids[1])
        self.assertFalse(result['same_process'])
        self.manager.route.assert_called_once_with(self.ids[1],prefer=True)

    def test_normal_mode_change_still_refuses_an_active_reply(self):
        self.manager.owned.return_value=False
        with self.assertRaisesRegex(SubscriptionError,'暂停并切换'):
            configure(self.manager)
        self.native.close_gui.assert_not_called()

    def test_reconnection_failure_leaves_saved_pause_without_rerouting(self):
        self.processing(); self.stop_api(); self.manager.owned.return_value=False
        with patch('science_hot_switch.configure', side_effect=SubscriptionError('reconnect failed')):
            with self.assertRaisesRegex(SubscriptionError, 'reconnect failed'):
                switch(self.manager, self.ids[1])
        self.assertEqual(self.statuses()[self.frame], 'cancelled')
        self.manager.route.assert_not_called()

    def test_already_connected_mode_reuses_busy_gateway(self):
        result=configure(self.manager)
        self.assertTrue(result['same_process'])
        self.manager.ensure_gateway.assert_not_called()
        self.native.close_gui.assert_not_called()

    def test_explicit_reconnect_stops_science_before_orphan_gateway(self):
        self.manager.owned.return_value=False
        def run(exe,args,**kwargs):
            if args==['stop']: self.running=False
            return SimpleNamespace(stdout='')
        self.manager.run.side_effect=run
        def stop(manager,interrupt):
            self.assertFalse(self.running)
            self.assertTrue(interrupt)
        self.native.launch.return_value={'running':True,'pid':456}
        self.manager.owned.side_effect=[False,True]
        with patch('science_hot_switch.stop_gateway',side_effect=stop):
            result=configure(self.manager,pause_active=True)
        self.assertFalse(result['same_process'])
        self.assertEqual(self.statuses()[self.frame],'cancelled')

    def test_gateway_interrupt_requires_owned_command_and_explicit_action(self):
        self.manager.health.return_value={'active_requests':1,'pid':123}
        with patch('science_hot_switch.os.kill') as kill:
            with self.assertRaises(SubscriptionError): stop_gateway(self.manager)
            self.manager.run.return_value.stdout='unrelated process'
            with self.assertRaisesRegex(SubscriptionError,'归属'): stop_gateway(self.manager,interrupt=True)
            kill.assert_not_called()
            script=Path(__file__).resolve().parents[1]/'subscription_gateway.py'
            self.manager.run.return_value.stdout=f'python3 -I {script} --root {self.store.root}'
            self.manager.health.side_effect=[{'active_requests':1,'pid':123},None]
            stop_gateway(self.manager,interrupt=True)
            kill.assert_called_once()


if __name__=='__main__': unittest.main()
