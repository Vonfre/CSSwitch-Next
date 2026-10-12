#!/usr/bin/env python3
"""Isolated installed-Science test with a mock model and real public APIs.

Never switches the active CSSwitch configuration. Results remain in .sandbox.
The outer macOS sandbox allows only loopback; a parent-side HTTPS tunnel
permits NCBI and UniProt. sandbox.hard_fail=false is confined to this nested
test because nested sandbox-exec functional probes fail on macOS.
"""
import argparse
import ast
import http.cookiejar
import select
import tempfile
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description="Probe public-science MCP in an isolated Claude Science session on macOS.")
parser.add_argument('--prototype', action='store_true', help='Use the earlier minimal Python prototype instead of transplanted OpenScience')
parser.add_argument('--science-bin', type=Path, help="Override the installed Science binary")
args = parser.parse_args()
original_home = Path.home()
candidate = original_home / '.claude-science/bin/claude-science'
binary = args.science_bin or (candidate if candidate.is_file() else Path('/Applications/Claude Science.app/Contents/Resources/bin/claude-science'))
if not binary.is_file():
    parser.error('Install Claude Science or pass --science-bin')
node = shutil.which('node')
if not node:
    parser.error('Node.js is needed only to create isolated test credentials')
(repo / '.sandbox').mkdir(exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='connector-probe-', dir=repo / '.sandbox'))
home = root / 'home'
home.mkdir()
auth = home / '.claude-science'
tools = root / 'tools'
tools.mkdir()
shim = tools / 'security'
shim.write_text('#!/bin/sh\nif [ "$1" = find-generic-password ]; then exit 44; fi\nexit 1\n')
shim.chmod(0o700)
binary = str(binary.resolve())
print('Isolated results:', root, flush=True)
subprocess.run([node, str(repo / 'scripts/make-virtual-oauth.mjs'), '--auth-dir', str(auth)], check=True, stdout=subprocess.DEVNULL)
config = root / 'science.toml'
config.write_text(f'data_dir = {json.dumps(str(auth))}\n[paths]\nauth_dir = {json.dumps(str(auth))}\n[conda]\nauto_install = false\n[sandbox]\nhard_fail = false\n[update]\nauto_update = false\n')

class Stub(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_CONNECT(self):
        host, _, port = self.path.rpartition(':')
        if host not in ('eutils.ncbi.nlm.nih.gov', 'rest.uniprot.org') or port != '443':
            self.send_response(401)
            self.end_headers()
            return
        upstream = None
        try:
            upstream = socket.create_connection((host, 443), timeout=20)
            self.send_response(200)
            self.end_headers()
            self.wfile.flush()
            channels = (self.connection, upstream)
            while True:
                ready, _, _ = select.select(channels, [], [], 25)
                if not ready:
                    break
                for channel in ready:
                    data = channel.recv(65536)
                    if not data:
                        return
                    (upstream if channel is self.connection else self.connection).sendall(data)
        except OSError:
            pass
        finally:
            if upstream:
                upstream.close()
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'object': 'list', 'data': [{'id': 'claude-sonnet-4-6', 'type': 'model', 'display_name': 'Offline probe'}]}).encode())
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        names = [t.get('name') for t in body.get('tools', [])]
        with (root / 'model-events.jsonl').open('a') as f:
            f.write(json.dumps({'path': self.path, 'tools': names, 'definitions': [t for t in body.get('tools', []) if t.get('name') in ('repl', 'skill')], 'messages': body.get('messages'), 'stream': body.get('stream')}) + '\n')
        tool_results = [c for m in body.get('messages', []) if isinstance(m.get('content'), list) for c in m['content'] if isinstance(c, dict) and c.get('type') == 'tool_result']
        want_pubmed = 'PUBMED_PROBE' in json.dumps(body.get('messages', []))
        want_uniprot = 'UNIPROT_PROBE' in json.dumps(body.get('messages', []))
        suffix = ('uniprot_fetch' if want_uniprot else 'pubmed_search' if want_pubmed else 'connector_probe') if args.prototype else ('science_fetch' if want_uniprot else 'science_search' if want_pubmed else 'science_list_dbs')
        if 'repl' in names and not tool_results:
            content = [{'type': 'tool_use', 'id': 'toolu_probe_skill', 'name': 'skill', 'input': {'skill': 'mcp-csswitch-probe', 'human_description': 'Loading scientific connector methods'}}]
            reason = 'tool_use'
        elif 'repl' in names and len(tool_results) == 1:
            arguments = (', accession="P04637"' if want_uniprot else ', query="BRCA1 breast cancer"' if want_pubmed else '') if args.prototype else (', db="uniprot", id="P04637"' if want_uniprot else ', db="pubmed", query="BRCA1 breast cancer", limit=2' if want_pubmed else '')
            code = f'result = host.mcp("csswitch-probe", "{suffix}"{arguments}); print(result)'
            if want_uniprot and not args.prototype:
                code = f'result = host.mcp("csswitch-probe", "{suffix}"{arguments}); print({{"db": result["db"], "id": result["id"], "found": result["found"], "record": {{k: result["record"][k] for k in ("primaryAccession", "genes", "organism")}}}})'
            content = [{'type': 'tool_use', 'id': 'toolu_probe_repl', 'name': 'repl', 'input': {'code': code, 'human_description': 'Calling read-only scientific connector'}}]
            reason = 'tool_use'
        else:
            text = 'Probe completed. Tool result: ' + json.dumps(tool_results[-1].get('content'), ensure_ascii=False) if tool_results else 'Offline connector probe.'
            content = [{'type': 'text', 'text': text}]
            reason = 'end_turn'
        msg = {'id': 'msg_connector_probe', 'type': 'message', 'role': 'assistant', 'model': body.get('model', 'claude-sonnet-4-6'), 'content': content, 'stop_reason': reason, 'stop_sequence': None, 'usage': {'input_tokens': 10, 'output_tokens': 10}}
        self.send_response(200)
        if not body.get('stream'):
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(msg).encode())
            return
        self.send_header('Content-Type', 'text/event-stream')
        self.end_headers()
        events = [('message_start', {'type': 'message_start', 'message': {**msg, 'content': [], 'stop_reason': None, 'usage': {'input_tokens': 10, 'output_tokens': 0}}})]
        for i, block in enumerate(content):
            empty = {**block, 'input': {}} if block['type'] == 'tool_use' else {'type': 'text', 'text': ''}
            events.append(('content_block_start', {'type': 'content_block_start', 'index': i, 'content_block': empty}))
            delta = {'type': 'input_json_delta', 'partial_json': json.dumps(block['input'])} if block['type'] == 'tool_use' else {'type': 'text_delta', 'text': block['text']}
            events.append(('content_block_delta', {'type': 'content_block_delta', 'index': i, 'delta': delta}))
            events.append(('content_block_stop', {'type': 'content_block_stop', 'index': i}))
        events.extend([('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': reason, 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})])
        for event, data in events:
            self.wfile.write(('event: ' + event + '\ndata: ' + json.dumps(data) + '\n\n').encode())
            self.wfile.flush()

stub = ThreadingHTTPServer(('127.0.0.1', 0), Stub)
threading.Thread(target=stub.serve_forever, daemon=True).start()
with socket.socket() as s:
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
base = f'http://127.0.0.1:{port}'
env = {k: v for k, v in os.environ.items() if k in ('LANG', 'LC_ALL', 'TMPDIR')}
env.update(HOME=str(home), PATH=f'{tools}:/usr/bin:/bin:/usr/sbin:/sbin', ANTHROPIC_BASE_URL=f'http://127.0.0.1:{stub.server_port}', https_proxy=f'http://127.0.0.1:{stub.server_port}', HTTPS_PROXY=f'http://127.0.0.1:{stub.server_port}', no_proxy='127.0.0.1,localhost,::1', NO_PROXY='127.0.0.1,localhost,::1', OPERON_DISABLE_NONESSENTIAL_TRAFFIC='1')
profile = '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))' + f'(deny file-write* (subpath {json.dumps(str(original_home / ".claude-science"))}))(deny file-read* (subpath {json.dumps(str(original_home / ".claude-science/.oauth-tokens"))}) (literal {json.dumps(str(original_home / ".claude-science/encryption.key"))}))'

launch_args = [binary, 'serve', '--data-dir', str(auth), '--config', str(config), '--port', str(port), '--sandbox-port', '0', '--detached', '--no-browser', '--no-auto-update']
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(jar))
report = []
(root / 'runtime.json').write_text(json.dumps({'port': port, 'home': str(home), 'config': str(config)}))
def call(path, body=None, method=None, quiet=False):
    headers = {'Origin': base}
    csrf = next((c.value for c in jar if c.name == 'operon_csrf'), None)
    if csrf: headers['x-operon-csrf'] = csrf
    if body is not None: headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
    try:
        with opener.open(req, timeout=25) as res:
            code, text = res.status, res.read().decode()
    except urllib.error.HTTPError as res:
        code, text = res.code, res.read().decode()
    try: value = json.loads(text)
    except ValueError: value = text[:300]
    if path not in ('/api/auth/nonce', '/api/csrf') and not quiet:
        item = {'path': path, 'status': code, 'result': value}
        report.append(item)
        shown = {'path': path, 'status': code}
        if '/directory-health' in path or '/tool-permissions' in path or code >= 400:
            shown['result'] = value
        print(json.dumps(shown, ensure_ascii=False), flush=True)
        (root / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    return code, value

try:
    with (root / 'science.log').open('w') as log:
        r = subprocess.run(['/usr/bin/sandbox-exec', '-p', profile] + launch_args, env=env, stdout=log, stderr=log, timeout=80)
    print('launch_exit', r.returncode, flush=True)
    if r.returncode: raise RuntimeError('Science failed to start; see science.log')
    for _ in range(80):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                break
        except OSError:
            time.sleep(.25)
    else:
        raise RuntimeError('Science did not bind its isolated port')
    out = subprocess.run([binary, 'url', '--data-dir', str(auth), '--config', str(config)], env=env, capture_output=True, text=True, timeout=15)
    url = next(line.strip() for line in out.stdout.splitlines() if line.startswith('http://'))
    nonce = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['nonce'][0]
    assert call('/api/auth/nonce', {'nonce': nonce, 'dest': '/'})[0] == 200
    csrf_code, csrf_body = call('/api/csrf')
    print('csrf_status', csrf_code, 'cookie_names', [c.name for c in jar], 'error', csrf_body if csrf_code >= 400 else '<ok>', flush=True)
    assert csrf_code in (200, 204)
    policy_code, policy = call('/api/org-policy')
    if policy_code != 200 or not policy['caps']['operon_custom_connectors']['allowed']:
        raise RuntimeError('Science policy does not allow custom connectors')
    call('/api/mcp-servers/directory-health')
    def manage(operation, params=None):
        link_output = subprocess.run([binary, 'url', '--data-dir', str(auth), '--config', str(config)], env=env, capture_output=True, text=True, timeout=15)
        link = next(line.strip() for line in link_output.stdout.splitlines() if line.startswith('http://'))
        private_nonce = urllib.parse.parse_qs(urllib.parse.urlsplit(link).query)['nonce'][0]
        request = {'base': base, 'nonce': private_nonce, 'operation': operation, 'params': params or {}}
        managed = subprocess.run(['/usr/bin/python3', str(repo / 'scripts/science-mcp-manage.py')], input=json.dumps(request), capture_output=True, text=True, timeout=90)
        value = json.loads(managed.stdout)
        if not value.get('ok'):
            raise RuntimeError('Management bridge: ' + value.get('message', 'failed'))
        assert 'operon_auth' not in managed.stdout and private_nonce not in managed.stdout
        return value['value']
    print('PASS management list', len(manage('list')['connectors']), flush=True)
    created = manage('add_local', {'name': 'csswitch-probe', 'command': '/usr/bin/python3' if args.prototype else node, 'args': [str(repo / 'scripts/science-public-mcp.py' if args.prototype else repo / 'scripts/openscience-mcp/server.mjs')], 'env': {'HTTPS_PROXY': f'http://127.0.0.1:{stub.server_port}', 'https_proxy': f'http://127.0.0.1:{stub.server_port}', 'HTTP_PROXY': f'http://127.0.0.1:{stub.server_port}', 'http_proxy': f'http://127.0.0.1:{stub.server_port}', 'NO_PROXY': '127.0.0.1,localhost,::1', 'no_proxy': '127.0.0.1,localhost,::1'}, 'description': 'Isolated read-only fixture'})
    connector = created['connector']
    sid = connector['id']
    manage('attach', {'id': sid})
    manage('detach', {'id': sid})
    manage('attach', {'id': sid})
    print('PASS management attach/detach', flush=True)
    assert manage('tools', {'id': sid})['status'] in ('loaded', 'pending')
    for _ in range(8):
        code, result = call('/api/mcp-servers/' + urllib.parse.quote(sid, safe='') + '/tool-permissions?source=local-stdio')
        if isinstance(result, dict) and result.get('status') not in ('pending',): break
        time.sleep(1)
    _, servers = call('/api/agents/OPERON/mcp-servers?include_tools=true', quiet=True)
    for server in servers:
        if server.get('source') == 'bundled':
            call('/api/mcp-servers/connectors/' + urllib.parse.quote(server['id'], safe='') + '/enabled', {'enabled': False}, 'PUT', quiet=True)
    for tool in (('connector_probe', 'pubmed_search', 'uniprot_fetch') if args.prototype else ('science_list_dbs', 'science_search', 'science_fetch')):
        manage('grant', {'id': sid, 'toolName': tool, 'decision': 'allow'})
    _, project = call('/api/projects', {'name': 'isolated-connector-probe'})
    pid = project.get('project_id', project.get('id'))
    for label, request in [('fixture', 'Call science_list_dbs once and report its result.' if not args.prototype else 'Call connector_probe once and report its result.'), ('pubmed', 'PUBMED_PROBE: Call pubmed_search once for BRCA1 breast cancer and report the returned papers.'), ('uniprot', 'UNIPROT_PROBE: Call uniprot_fetch once for P04637 and report the gene and organism.')]:
        _, frame = call('/api/frames', {'project_id': pid})
        fid = frame['root_frame_id']
        call('/api/frames/' + fid + '/session-config', {'verifier_mode': 'off', 'memory_mode': 'off', 'plan_mode': False, 'ultra_mode': False})
        code, accepted = call('/api/frames/' + fid + '/message', {'input_data': {'request': request, 'plan_mode': False}, 'model': 'claude-sonnet-4-6', 'thinking': False})
        if code >= 400:
            raise RuntimeError(label + ': Science rejected the test message')
        final = None
        for _ in range(45):
            _, final = call('/api/frames/' + fid + '?shallow=true', quiet=True)
            status = final.get('status') if isinstance(final, dict) else None
            if status and status != 'processing':
                break
            time.sleep(1)
        print('frame_final', json.dumps({'status': final.get('status'), 'response': final.get('output_data')}, ensure_ascii=False)[:2000], flush=True)
        _, messages = call('/api/frames/' + fid + '/messages', quiet=True)
        (root / (label + '-transcript.json')).write_text(json.dumps(messages, ensure_ascii=False, indent=2))
        results = [c for m in messages.get('messages', []) if isinstance(m.get('content'), list) for c in m['content'] if c.get('type') == 'tool_result' and c.get('tool_use_id') == 'toolu_probe_repl']
        if final.get('status') != 'completed' or not results:
            raise RuntimeError(label + ': tool execution did not complete')
        cell = json.loads(results[-1]['content'])
        if cell.get('exit_code') != 0:
            raise RuntimeError(label + ': REPL call failed: ' + str(cell.get('stderr')))
        data = ast.literal_eval(cell['stdout'].strip())
        marker = data.get('status') == 'CSSWITCH_MCP_PROBE_OK' if label == 'fixture' else bool(data.get('results')) if label == 'pubmed' else data.get('accession') == 'P04637' and 'TP53' in data.get('genes', [])
        if not args.prototype:
            marker = len(data.get('databases', [])) >= 40 if label == 'fixture' else bool(data.get('results')) if label == 'pubmed' else data.get('found') and data.get('record', {}).get('primaryAccession') == 'P04637' and 'TP53' in json.dumps(data['record'].get('genes'))
        if not marker:
            raise RuntimeError(label + ': public source result failed validation')
        (root / (label + '-result.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print('PASS', label, json.dumps(data, ensure_ascii=False)[:700], flush=True)

    manage('remove', {'id': sid})
    assert all(c['id'] != sid for c in manage('list')['connectors'])
    print('PASS management remove', flush=True)

finally:
    stopped = subprocess.run([binary, 'stop', '--data-dir', str(auth), '--config', str(config)], env=env, capture_output=True, text=True, timeout=20)
    print('stop_exit', stopped.returncode, flush=True)
    stub.shutdown()
    if stopped.returncode:
        raise RuntimeError('Could not stop the isolated Science process; use runtime.json to identify it')
