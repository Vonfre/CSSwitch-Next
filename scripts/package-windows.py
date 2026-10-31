#!/usr/bin/env python3
"""Package an existing Windows build, with optional self-contained runtimes."""
import argparse
from pathlib import Path
import shutil
import zipfile

parser=argparse.ArgumentParser()
parser.add_argument('--binary',required=True,type=Path)
parser.add_argument('--runtime',type=Path)
parser.add_argument('--output',required=True,type=Path)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
destination=args.output.resolve()
if destination.exists() and any(destination.iterdir()):
    raise SystemExit('Output directory must be empty; existing files were not changed.')
destination.mkdir(parents=True,exist_ok=True)
shutil.copy2(args.binary,destination/'CSSwitch Next.exe')
for name in ('proxy','scripts'):
    shutil.copytree(root/name,destination/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','tests'))
for name in ('LICENSE','LICENSE-MIT','NOTICE'):
    shutil.copy2(root/name,destination/name)
shutil.copy2(root/'docs/WINDOWS.md',destination/'Windows使用说明.md')
if args.runtime:
    for name in ('python','node'):
        shutil.copytree(args.runtime/name,destination/'runtime'/name)
with zipfile.ZipFile(str(destination)+'.zip','w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
    for path in destination.rglob('*'):
        if path.is_file(): archive.write(path,path.relative_to(destination.parent))
print(destination)
print(str(destination)+'.zip')
