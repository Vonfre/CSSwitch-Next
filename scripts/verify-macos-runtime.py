#!/usr/bin/env python3
"""Validate a relocated runtime using no PATH, Science environment, or user Python packages."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def verify(python, scripts):
    python, scripts = python.resolve(), scripts.resolve()
    code = '''import sys, ssl, sqlite3, tomllib, platform, json, os
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
assert sys.version_info >= (3, 11)
assert sys.prefix in sys.executable
f=Fernet(Fernet.generate_key()); assert f.decrypt(f.encrypt(b'portable'))==b'portable'
a=AESGCM(bytes(32)); assert a.decrypt(bytes(12), a.encrypt(bytes(12), b'portable', None), None)==b'portable'
assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
assert os.path.isfile(os.environ['SSL_CERT_FILE'])
sys.path.insert(0,sys.argv[1])
import science_paths, science_native_switch, subscription_gateway, subscription_usage
print(json.dumps({'python':platform.python_version(),'arch':platform.machine(),'ssl':ssl.OPENSSL_VERSION,'cryptography':'ok','tomllib':'ok','sqlite':'ok','ca_roots':'ok','isolated':sys.flags.isolated}))
'''
    with tempfile.TemporaryDirectory(prefix='csswitch-clean-home-') as home:
        env = {'HOME':str(Path(home).resolve()), 'PATH':'/usr/bin:/bin', 'LANG':'en_US.UTF-8', 'PYTHONHOME':'/nonexistent', 'PYTHONPATH':'/nonexistent'}
        result = subprocess.run([str(python), '-I', '-c', code, str(scripts)], env=env, capture_output=True, text=True, timeout=30, check=True)
        print(result.stdout.strip())
        # A fresh machine must be able to open subscriptions before installing Science.
        result = subprocess.run([str(python), '-I', str(scripts/'science-subscriptions.py'), 'status'], env=env, capture_output=True, text=True, timeout=30, check=True)
        value=json.loads(result.stdout)
        assert value['ok'] and not value['value']['accounts']
        print('Clean-home subscription startup passed')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python',type=Path,required=True)
    parser.add_argument('--scripts',type=Path,default=Path(__file__).resolve().parent)
    args=parser.parse_args();verify(args.python,args.scripts)
