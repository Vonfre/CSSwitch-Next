"""Loopback fixture for optional native conversation-model verification."""
import json
from http.server import BaseHTTPRequestHandler

class ModelUpstream(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        body = json.dumps({'data': [{'id': 'fixture-gpt'}, {'id': 'fixture-claude'}]}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self.server.hits.append({'path': self.path, 'model': data.get('model')})
        model = data.get('model', '')
        message = {'id': 'msg_fixture', 'type': 'message', 'role': 'assistant', 'model': model,
                   'content': [{'type': 'text', 'text': 'OK'}], 'stop_reason': 'end_turn', 'stop_sequence': None,
                   'usage': {'input_tokens': 10, 'output_tokens': 1}}
        if self.path.endswith('/responses'):
            result = {'id':'resp_fixture','status':'completed','output':[{'type':'message','role':'assistant','content':[{'type':'output_text','text':'OK'}]}], 'usage': {'input_tokens':10,'output_tokens':1}}
            events = [{'type':'response.output_text.delta','output_index':0,'delta':'OK'}, {'type':'response.completed','response':result}]
        elif self.path.endswith('/chat/completions'):
            result = {'id':'chat_fixture','choices':[{'message':{'role':'assistant','content':'OK'},'finish_reason':'stop'}], 'usage':{'prompt_tokens':10,'completion_tokens':1}}
            events = [{'choices':[{'delta':{'content':'OK'},'finish_reason':None}]}, {'choices':[{'delta':{},'finish_reason':'stop'}],'usage':result['usage']}]
        else:
            result = message
            events = [{'type':'message_start','message':{**message,'content':[],'stop_reason':None}},
                      {'type':'content_block_start','index':0,'content_block':{'type':'text','text':''}},
                      {'type':'content_block_delta','index':0,'delta':{'type':'text_delta','text':'OK'}},
                      {'type':'content_block_stop','index':0},
                      {'type':'message_delta','delta':{'stop_reason':'end_turn','stop_sequence':None},'usage':{'output_tokens':1}},
                      {'type':'message_stop'}]
        if data.get('stream'):
            body = ''.join(('event: '+e['type']+'\n' if 'type' in e else '')+'data: '+json.dumps(e)+'\n\n' for e in events).encode()
            kind = 'text/event-stream'
        else:
            body = json.dumps(result).encode(); kind='application/json'
        self.send_response(200); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
