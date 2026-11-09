#!/usr/bin/env python3
"""Build-time only: bundle a pinned, relocatable Python; never install into the user's Python."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

VERSION = '3.13.18'
RELEASE = '20261026'
ARCHIVES = {
    'arm64': ('aarch64', '8392b6ea51d94391befb2c31ebe75463f045d0b69dfcf5e1bb3234e1f5fa3405'),
    'x64': ('x86_64', '7412c19d896c99431b74a18a5643e110449a7f9590fbab179c3e5ab27b9aa9e3'),
}
SITE_CUSTOMIZE = '''# CSSwitch's private runtime: ship CA roots and avoid writing into the signed app.
import os, sys
from pathlib import Path
sys.dont_write_bytecode = True
os.environ.setdefault('SSL_CERT_FILE', str(Path(__file__).parent / 'certifi' / 'cacert.pem'))
'''
MACHO = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca', b'\xca\xfe\xba\xbf'}


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def unpack(archive, destination):
    # Support the system Python 3.9 used only to prepare the build.
    with tarfile.open(archive) as package:
        for member in package.getmembers():
            target = (destination / member.name).resolve()
            if not target.is_relative_to(destination.resolve()) or member.isdev() or member.isfifo():
                raise ValueError('Unsafe runtime archive member')
            if member.issym() or member.islnk():
                link = (target.parent if member.issym() else destination) / member.linkname
                if not link.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Unsafe runtime archive link')
        if sys.version_info >= (3, 12): package.extractall(destination, filter='data')
        else: package.extractall(destination)


def prepare(destination, arch):
    if sys.platform != 'darwin': raise ValueError('Prepare macOS runtimes on a macOS build host.')
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    cpu, expected = ARCHIVES[arch]
    requirements = Path(__file__).with_name('macos-runtime-requirements.txt')
    fingerprint = hashlib.sha256((expected + requirements.read_text() + SITE_CUSTOMIZE).encode()).hexdigest()
    target = destination / 'python'
    marker = target / '.csswitch-runtime.json'
    if marker.is_file() and json.loads(marker.read_text()).get('fingerprint') == fingerprint and (target / 'bin/python3').is_file():
        print('Verified runtime already prepared:', target)
        return
    archive = destination / ('cpython-' + arch + '.tar.gz')
    if not archive.is_file() or digest(archive) != expected:
        name = 'cpython-{}%2B{}-{}-apple-darwin-install_only_stripped.tar.gz'.format(VERSION, RELEASE, cpu)
        url = 'https://github.com/astral-sh/python-build-standalone/releases/download/' + RELEASE + '/' + name
        print('Downloading pinned Python for ' + arch, flush=True)
        partial = archive.with_suffix('.partial')
        with urllib.request.urlopen(url, timeout=120) as response, partial.open('wb') as out:
            shutil.copyfileobj(response, out)
        if digest(partial) != expected:
            partial.unlink()
            raise ValueError('Python archive checksum mismatch')
        partial.replace(archive)
    with tempfile.TemporaryDirectory(prefix='.prepare-', dir=destination) as tmp:
        staging = Path(tmp)
        unpack(archive, staging)
        runtime = staging / 'python'
        site = runtime / ('lib/python' + '.'.join(VERSION.split('.')[:2]) + '/site-packages')
        # The host pip installs wheels for the target, without running target code.
        host = str(runtime / 'bin/python3') if platform.machine() in (cpu, arch) else sys.executable
        subprocess.run([host, '-I', '-m', 'pip', '--isolated', 'install', '--index-url', 'https://pypi.org/simple', '--require-hashes', '--only-binary=:all:', '--no-compile',
                        '--platform', 'macosx_11_0_' + ('arm64' if arch == 'arm64' else 'x86_64'),
                        '--python-version', '3.13', '--implementation', 'cp', '--abi', 'cp313',
                        '--target', str(site), '-r', str(requirements)], check=True)
        (site / 'sitecustomize.py').write_text(SITE_CUSTOMIZE)
        for cache in runtime.rglob('__pycache__'):
            shutil.rmtree(cache)
        # Resource executables/extensions must be signed before signing the outer app.
        for path in runtime.rglob('*'):
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as stream: magic = stream.read(4)
                if magic in MACHO:
                    subprocess.run(['codesign', '--force', '--sign', '-', str(path)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        (runtime / '.csswitch-runtime.json').write_text(json.dumps({'python': VERSION, 'arch': arch, 'archive_sha256': expected, 'fingerprint': fingerprint}))
        old = destination / 'python.previous'
        if old.exists(): shutil.rmtree(old)
        if target.exists(): target.rename(old)
        try: runtime.rename(target)
        except Exception:
            if old.exists(): old.rename(target)
            raise
        if old.exists(): shutil.rmtree(old)
    print('Bundled runtime ready:', target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / '.runtime/macos')
    parser.add_argument('--arch', choices=ARCHIVES, default='arm64' if platform.machine() == 'arm64' else 'x64')
    args = parser.parse_args()
    prepare(args.output, args.arch)
