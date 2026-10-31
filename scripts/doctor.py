#!/usr/bin/env python3
"""Portable, read-only diagnostic entry point. Never print credentials."""
import json
import os
from pathlib import Path
import platform
import shutil
import socket

print('CSSwitch Next diagnostics')
print('System:', platform.system(), platform.machine())
print('Python:', platform.python_version())
print('Node:', 'available' if shutil.which('node') else 'not found')
print('Science data:', 'available' if (Path.home()/'.claude-science').is_dir() else 'not found')
for name, default in [('PROXY', 18992), ('SANDBOX', 8991)]:
    port=int(os.environ.get('CSSWITCH_'+name+'_PORT', default))
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=1): pass
        status='listening'
    except OSError: status='not listening'
    print(name+':', port, status)
