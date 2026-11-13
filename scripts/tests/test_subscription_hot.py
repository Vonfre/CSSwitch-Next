"""Request-boundary switching against a local, deterministic SSE upstream."""
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from subscription_store import Store, SubscriptionError
from subscription_gateway import Gateway


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers['Content-Length']))
        token = self.headers['Authorization']
        self.server.seen.append((token, body, self.headers.get('x-api-key')))
        if getattr(self.server, 'error_body', None):
            raw = json.dumps(self.server.error_body).encode()
            self.send_response(400); self.send_header('Content-Length', str(len(raw)))
            self.send_header('request-id', 'req_fixture'); self.end_headers(); self.wfile.write(raw); return
        if token in self.server.reject:
            self.send_response(429); self.send_header('Content-Length','0'); self.send_header('Retry-After','60'); self.end_headers(); return
        self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
        self.wfile.write(b'data: {"type":"message_start"}\n\n'); self.wfile.flush()
        if self.server.hold:
            self.server.started.set(); self.server.release.wait(5)
        if not self.server.truncate:
            self.wfile.write(b'data: {"type":"message_stop"}\n\n'); self.wfile.flush()


class HotRoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=Store(Path(self.temp.name).resolve()/'subscriptions')
        self.ids=[self.store.add({'account_uuid':s,'email':s+'@example.test','access_token':s,
            'refresh_token':'refresh-'+s,'expires_at':time.time()+3600}) for s in ('A','B')]
        with self.store.locked():
            state=self.store.read();state.update(native_mode=True,hot_mode=True,mode='manual',selected=self.ids[0]);self.store.write(state)
        self.up=ThreadingHTTPServer(('127.0.0.1',0),Upstream)
        self.up.seen=[];self.up.reject=set();self.up.hold=False;self.up.truncate=False
        self.up.started=threading.Event();self.up.release=threading.Event()
        self.gateway=Gateway(('127.0.0.1',0),self.store,'http://127.0.0.1:'+str(self.up.server_port))
        for server in (self.up,self.gateway):
            threading.Thread(target=server.serve_forever,daemon=True).start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        self.url=f'http://127.0.0.1:{self.gateway.server_port}/{self.gateway.secret}/v1/messages'
        self.body=b'{"model":"fixture","messages":[{"role":"user","content":"same conversation"}]}'
    def request(self):
        req=urllib.request.Request(self.url,data=self.body,headers={'Content-Type':'application/json','x-api-key':'must-not-forward'})
        with urllib.request.urlopen(req,timeout=8) as response:return response.read()

    def test_next_request_changes_account_and_preserves_transcript_bytes(self):
        self.request(); self.store.route(self.ids[1]); self.request()
        self.assertEqual([x[0] for x in self.up.seen],['Bearer A','Bearer B'])
        self.assertTrue(all(x[1]==self.body and x[2] is None for x in self.up.seen))
        self.assertEqual(self.gateway.last_inference['account_id'],self.ids[1])

    def test_switch_during_stream_keeps_original_request_and_uses_next_account(self):
        self.up.hold=True
        with ThreadPoolExecutor() as pool:
            pending=pool.submit(self.request)
            self.assertTrue(self.up.started.wait(3))
            self.store.route(self.ids[1])
            self.up.hold=False;self.up.release.set()
            self.assertIn(b'message_stop',pending.result(5))
        self.assertEqual(len(self.up.seen),1)
        self.assertEqual(self.store.snapshot()['selected'],self.ids[1])
        self.request()
        self.assertEqual([x[0] for x in self.up.seen],['Bearer A','Bearer B'])

    def test_auto_fails_over_only_explicit_rejection_before_stream(self):
        self.store.route();self.up.reject={'Bearer A'}
        self.assertIn(b'message_stop',self.request())
        self.assertEqual([x[0] for x in self.up.seen],['Bearer A','Bearer B'])
        self.assertEqual(self.store.snapshot()['selected'],self.ids[1])
        self.assertGreater(self.store.snapshot()['accounts'][self.ids[0]]['cooldown_until'],time.time())

    def test_explicit_priority_survives_failover_and_returns_after_cooldown(self):
        a, b = self.ids
        self.store.route(a, prefer=True)
        self.up.reject={'Bearer A'}
        self.request()
        self.assertEqual(self.store.snapshot()['selected'], b)
        self.assertEqual(self.store.public()['preferred_id'], a)
        self.assertEqual(self.store.public()['next_account_id'], b)
        self.request()
        self.assertEqual(self.up.seen[-1][0], 'Bearer B')
        with self.store.locked():
            state=self.store.read();state['accounts'][a]['cooldown_until']=0;self.store.write(state)
        self.up.reject=set()
        self.assertEqual(self.store.public()['next_account_id'], a)
        self.request()
        self.assertEqual(self.up.seen[-1][0], 'Bearer A')
        self.store.route(b)
        self.request()
        self.assertEqual(self.up.seen[-1][0], 'Bearer B')
        self.assertEqual(self.store.public()['preferred_id'], a)
        self.store.route()
        self.request()
        self.assertEqual(self.up.seen[-1][0], 'Bearer A')

    def test_priority_change_during_stream_is_not_overwritten_by_completion(self):
        a,b=self.ids
        self.store.route(a, prefer=True);self.up.hold=True
        with ThreadPoolExecutor() as pool:
            pending=pool.submit(self.request)
            self.assertTrue(self.up.started.wait(3))
            self.store.route(b, prefer=True)
            self.up.hold=False;self.up.release.set();pending.result(5)
        self.assertEqual(self.store.public()['preferred_id'],b)
        self.assertEqual(self.store.public()['next_account_id'],b)
        self.request();self.assertEqual([x[0] for x in self.up.seen],['Bearer A','Bearer B'])

    def test_partial_stream_never_replays_on_another_account(self):
        self.store.route();self.up.truncate=True
        self.assertIn(b'not replayed',self.request())
        self.assertEqual(len(self.up.seen),1)
        self.assertIsNone(self.gateway.last_inference)

    def test_history_rejection_is_classified_without_replay_or_content_disclosure(self):
        self.store.route()
        self.up.error_body={'error': {'type': 'invalid_request_error', 'message': 'Invalid signature in thinking block: PRIVATE_ECHO'}}
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request()
        result=caught.exception.read().decode()
        self.assertIn('历史消息的签名',result)
        self.assertNotIn('PRIVATE_ECHO',result)
        self.assertEqual(len(self.up.seen),1)
        self.assertEqual(self.up.seen[0][1],self.body)
        self.assertEqual(self.gateway.last_failure['kind'],'history_signature')
        self.assertEqual(self.gateway.last_failure['request_id'],'req_fixture')
        self.assertNotIn('PRIVATE_ECHO',json.dumps(self.gateway.last_failure))

    def test_safeguards_rejection_is_never_switched_or_replayed(self):
        self.store.route()
        self.up.error_body={'error': {'type': 'invalid_request_error', 'message': 'safeguards: reasoning_extraction'}}
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request()
        self.assertIn('安全策略',caught.exception.read().decode())
        self.assertEqual(self.gateway.last_failure['kind'],'safeguards_refusal')
        self.assertEqual(len(self.up.seen),1)

    def test_unknown_validation_error_keeps_redacted_field_explanation(self):
        self.up.error_body={'error': {'type': 'invalid_request_error', 'message': 'messages.4.content.0: unsupported field. sk-secret-token user@example.test'}}
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request()
        result=caught.exception.read().decode()
        self.assertIn('messages.4.content.0: unsupported field',result)
        self.assertNotIn('sk-secret-token',result)
        self.assertNotIn('user@example.test',result)
        self.assertEqual(len(self.up.seen),1)

    def test_encrypted_search_result_error_preserves_science_recovery_signal(self):
        self.store.route(self.ids[1], prefer=True)
        self.up.error_body={'error': {'type': 'invalid_request_error', 'message': 'messages.3.content.0: Invalid `encrypted_content` in `search_result` block'}}
        with self.assertRaises(urllib.error.HTTPError) as caught: self.request()
        self.assertEqual(caught.exception.code,400)
        result=json.loads(caught.exception.read())
        # Science 0.1.72 uses this exact error marker for its native one-shot
        # server-tool-result recovery; a translated generic error breaks Resume.
        self.assertIn('encrypted_content',result['error']['message'])
        self.assertIn('search_result',result['error']['message'])
        self.assertEqual(len(self.up.seen),1)
        self.assertEqual(self.up.seen[0][0],'Bearer B')
        self.assertEqual(self.up.seen[0][1],self.body)

    def test_native_slots_are_available_only_in_explicit_hot_mode(self):
        with self.store.locked():
            state=self.store.read();a=state['accounts'][self.ids[0]];a['native_owned']=True
            a.pop('access_token');a.pop('refresh_token');self.store.write(state)
        self.store.route(self.ids[0])
        with patch('science_hot_switch.native_token',return_value='NATIVE') as read:
            self.request();self.assertEqual(read.call_args.args[1],self.ids[0])
        self.assertEqual(self.up.seen[0][0],'Bearer NATIVE')
        with self.store.locked():
            state=self.store.read();state['hot_mode']=False;self.store.write(state)
        self.assertEqual(self.gateway.candidates()[1],[])
        with self.assertRaises(SubscriptionError): self.gateway.token(self.ids[0])


if __name__=='__main__':unittest.main()
