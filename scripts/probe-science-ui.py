#!/usr/bin/env python3
"""Optional installed-Science browser check: isolated HOME and loopback-only sandbox.
Requires macOS, Node and Playwright Chromium. Never changes the user instance.
"""
import sys
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse

repo = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--settings', action='store_true', help='Inspect every native settings module')
parser.add_argument('--collect', action='store_true', help='Collect native settings text before asserting translations')
options = parser.parse_args()
candidate = Path.home() / '.claude-science/bin/claude-science'
binary = candidate if candidate.is_file() else Path('/Applications/Claude Science.app/Contents/Resources/bin/claude-science')
if not binary.is_file() or not shutil.which('node'):
 raise SystemExit('Install Claude Science and Node before running the optional probe')
(repo / '.sandbox').mkdir(exist_ok=True)
root = Path(tempfile.mkdtemp(prefix='management-probe-', dir=repo / '.sandbox'))
fixture_home = root / 'home'; fixture_home.mkdir()
auth = fixture_home / '.claude-science'
subprocess.run([shutil.which('node'), str(repo / 'scripts/make-virtual-oauth.mjs'), '--auth-dir', str(auth)], check=True, stdout=subprocess.DEVNULL)
tools = root / 'tools'; tools.mkdir()
shim = tools / 'security'; shim.write_text('#!/bin/sh\nif [ "$1" = find-generic-password ]; then exit 44; fi\nexit 1\n'); shim.chmod(0o700)
config = root / 'science.toml'
config.write_text('data_dir = ' + json.dumps(str(auth)) + '\n[paths]\nauth_dir = ' + json.dumps(str(auth)) + '\n[conda]\nauto_install = false\n[sandbox]\nhard_fail = false\n[connectors]\ndisabled = ["pubmed", "biorxiv", "chembl", "clinical-trials", "https://pubmed.mcp.claude.com", "https://hcls.mcp.claude.com"]\n[update]\nauto_update = false\n')
with socket.socket() as sock:
 sock.bind(('127.0.0.1',0)); port = sock.getsockname()[1]
env = {k:v for k,v in os.environ.items() if k in ('LANG','LC_ALL','TMPDIR')}
env.update({'HOME': str(fixture_home), 'PATH': str(tools) + ':/usr/bin:/bin:/usr/sbin:/sbin', 'OPERON_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'ANTHROPIC_BASE_URL': 'http://127.0.0.1:9', 'HTTPS_PROXY': 'http://127.0.0.1:9', 'https_proxy': 'http://127.0.0.1:9'})
for key in ['ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'OPENAI_API_KEY', 'BUN_OPTIONS']: env.pop(key, None)
args = ['--data-dir', str(auth), '--config', str(config)]
profile = '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))'
spec = importlib.util.spec_from_file_location('bridge', repo / 'scripts/science-mcp-manage.py'); bridge = importlib.util.module_from_spec(spec); spec.loader.exec_module(bridge)
try:
 with (root/'science.log').open('w') as log:
  subprocess.run(['/usr/bin/sandbox-exec','-p',profile,str(binary),'serve',*args,'--port',str(port),'--sandbox-port','0','--detached','--no-browser','--no-auto-update'],env=env,stdout=log,stderr=log,timeout=50,check=True)
 for _ in range(80):
  try:
   with socket.create_connection(('127.0.0.1',port),timeout=1): break
  except OSError: time.sleep(.25)
 else: raise RuntimeError('Isolated Science did not start')
 link = subprocess.run([str(binary),'url',*args],env=env,capture_output=True,text=True,timeout=15,check=True)
 url = next(line for line in link.stdout.splitlines() if line.startswith('http://'))
 nonce = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)['nonce'][0]
 client = bridge.Bridge('http://127.0.0.1:'+str(port),nonce,str(auth),str(auth))

 ui_config = root/'ui-config.json'; ui_config.write_text('{"science_zh":true}')
 ready=root/'ui-ready.json'
 ui_proc=subprocess.Popen([sys.executable,str(repo/'scripts/science-ui-proxy.py'),'--upstream-port',str(port),'--config',str(ui_config),'--ready',str(ready)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  for _ in range(50):
   if ready.exists(): break
   time.sleep(.1)
  ui_port=json.loads(ready.read_text())['port']
  fresh=subprocess.run([str(binary),'url',*args],env=env,capture_output=True,text=True,timeout=15,check=True)
  link=next(line for line in fresh.stdout.splitlines() if line.startswith('http://'))
  u=urllib.parse.urlsplit(link)
  (root/'browser-input.json').write_text(json.dumps({'url':urllib.parse.urlunsplit(('http','127.0.0.1:'+str(ui_port),u.path,u.query,'')),'config':str(ui_config),'root':str(root)}))
  script = 'native-science-settings.mjs' if options.settings else 'native-science-ui.mjs'
  browser_args = [shutil.which('node'), str(repo/'desktop/tests'/script), str(root/'browser-input.json')]
  if options.collect: browser_args.append('--collect')
  result=subprocess.run(browser_args,capture_output=True,text=True,timeout=180 if options.settings else 90)
  print(result.stdout,flush=True)
  if result.returncode: print(__import__('re').sub(r'nonce=[^\s&]+', 'nonce=[REDACTED]', result.stderr[:1800]));raise RuntimeError('Native browser test failed')
 finally:
  ui_proc.terminate();ui_proc.wait(timeout=10)
finally:
 subprocess.run([str(binary),'stop',*args],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
 print('FIXTURE',root,flush=True)
