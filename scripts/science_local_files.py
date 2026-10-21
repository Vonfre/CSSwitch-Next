"""Open authenticated Science artifact records in macOS, without downloading copies."""
import http.client
import json
from pathlib import Path
import re
import stat
import subprocess
import sys


class FileActionError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')
ACTIONS = {'reveal', 'open', 'edit'}
# LaunchServices associations for scripts and applications can execute them.
# Unknown formats remain available through Finder or an explicit text editor.
DOCUMENTS = frozenset(('pdf txt md markdown csv tsv json jsonl yaml yml toml xml '
    'doc docx odt rtf ppt pptx odp xls xlsx ods png jpg jpeg gif webp svg tif tiff '
    'bmp heic avif mp3 wav m4a mp4 mov webm fasta fa fna fastq fq pdb cif sdf').split())


def native_json(port, headers, endpoint):
    credentials = {k: headers[k] for k in ('Cookie', 'Authorization') if headers.get(k)}
    if not credentials:
        raise FileActionError('Science 登录已失效，请从 CSSwitch 重新打开页面。', 401)
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=8)
    try:
        conn.request('GET', endpoint, headers={**credentials, 'Accept': 'application/json'})
        response = conn.getresponse()
        if response.status in (401, 403):
            raise FileActionError('Science 登录已失效，请从 CSSwitch 重新打开页面。', 401)
        if response.status != 200:
            raise FileActionError('无法读取此文件的 Science 存储记录。', 502)
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise FileActionError('文件版本记录过大，无法确认本地文件。', 502)
        return json.loads(raw)
    except (OSError, ValueError, http.client.HTTPException) as error:
        raise FileActionError('Science 文件信息暂不可用，请稍后重试。', 502) from error
    finally:
        conn.close()


def resolve_artifact(payload, request):
    if not isinstance(payload, dict) or set(payload) - {'artifactId', 'versionId', 'action'}:
        raise FileActionError('只支持通过 Science 文件记录打开本地文件。')
    artifact, version = payload.get('artifactId'), payload.get('versionId')
    if not isinstance(artifact, str) or not ID.fullmatch(artifact):
        raise FileActionError('未能确认当前文件。')
    if version is not None and (not isinstance(version, str) or not ID.fullmatch(version)):
        raise FileActionError('未能确认当前文件版本。')
    if not isinstance(payload.get('action'), str) or payload['action'] not in ACTIONS:
        raise FileActionError('不支持此文件操作。')
    prefix = '/api/artifacts/' + artifact
    metadata = request(prefix + '/metadata')
    if not isinstance(metadata, dict):
        raise FileActionError('文件存储记录格式不受支持。', 422)
    selected = metadata
    if version is not None and version != metadata.get('version_id'):
        versions = request(prefix + '/versions')
        matches = [v for v in versions if isinstance(v, dict) and v.get('artifact_id') == artifact
                   and v.get('version_id') == version] if isinstance(versions, list) else []
        if len(matches) != 1:
            raise FileActionError('找不到所选版本的本地文件，未打开其他版本。', 404)
        selected = matches[0]
    path = selected.get('file_path') or (metadata.get('ref_host_path') if selected is metadata else None)
    if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '\x00' in path:
        raise FileActionError('此文件没有可用的本地路径；远程文件请使用 Science 的下载功能。', 404)
    try:
        resolved = Path(path).resolve(strict=True)
        info = resolved.stat()
    except (OSError, ValueError, RuntimeError) as error:
        raise FileActionError('本地文件已移动或删除，无法打开。', 404) from error
    if not stat.S_ISREG(info.st_mode):
        raise FileActionError('此记录不是可打开的普通文件。', 422)
    return resolved, info


def open_artifact(payload, request, enabled=lambda: True):
    path, before = resolve_artifact(payload, request)
    if sys.platform != 'darwin':
        raise FileActionError('本地文件插件目前支持 macOS。', 422)
    action = payload['action']
    if action == 'open' and (before.st_mode & 0o111 or path.suffix.lower().lstrip('.') not in DOCUMENTS):
        raise FileActionError('此文件类型请使用“用文本编辑器打开”或“在文件夹中显示”。', 422)
    try:
        if path.stat() != before:
            raise FileActionError('文件已变化，请重新打开文件菜单后重试。', 409)
        if not enabled():
            raise FileActionError('本地文件插件已关闭。', 403)
        args = ['/usr/bin/open', *({'reveal': ['-R'], 'edit': ['-t'], 'open': []}[action]), str(path)]
        subprocess.run(args, check=True, timeout=10, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as error:
        raise FileActionError('本机应用未能打开文件，请检查默认应用关联，或在文件夹中显示。', 422) from error
    return {'message': {'reveal': '已在 Finder 中显示。', 'open': '已交给默认应用打开。', 'edit': '已交给默认文本编辑器打开。'}[action]}
