#!/usr/bin/env python3
"""Fetch pinned official Windows runtimes and verify their SHA-256 digests."""
import argparse
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

RUNTIMES = (
    ('python', 'https://www.python.org/ftp/python/3.14.0/python-3.14.0-embed-amd64.zip',
     '8d4d3590c10449d78aa4375f534e6d5f3027d67fdc362dd1a882279db6f90fdf'),
    ('node', 'https://nodejs.org/dist/v24.14.0/node-v24.14.0-win-x64.zip',
     '313fa40c0d7b18575821de8cb17483031fe07d95de5994f6f435f3b345f85c66'),
)


def prepare(destination):
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)
    for name, url, expected in RUNTIMES:
        target = destination / name
        marker = target / '.sha256'
        if marker.is_file() and marker.read_text().strip() == expected:
            continue
        if target.exists():
            raise ValueError(f'{target} already exists without the expected checksum marker; use a fresh output directory.')
        with tempfile.TemporaryDirectory(prefix='csswitch-runtime-') as tmp:
            archive = Path(tmp) / 'runtime.zip'
            print(f'Downloading {url}', flush=True)
            with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as out:
                shutil.copyfileobj(response, out)
            with archive.open('rb') as downloaded:
                digest = hashlib.file_digest(downloaded, 'sha256').hexdigest()
            if digest != expected:
                raise ValueError(f'Checksum mismatch for {name}')
            unpacked = Path(tmp) / 'unpacked'
            with zipfile.ZipFile(archive) as package:
                package.extractall(unpacked)
            if name == 'python':
                shutil.copytree(unpacked, target)
                (target / 'python314._pth').write_text('python314.zip\n.\n../../proxy\n../../scripts\n', encoding='utf-8')
            else:
                source = unpacked / 'node-v24.14.0-win-x64'
                target.mkdir()
                for file in ('node.exe', 'LICENSE', 'README.md'):
                    shutil.copy2(source / file, target / file)
            marker.write_text(expected + '\n', encoding='ascii')
    print(f'Windows runtimes ready: {destination}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / '.runtime')
    prepare(parser.parse_args().output)
