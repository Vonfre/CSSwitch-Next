#!/usr/bin/env python3
"""Read Science databases without changing them; manage CSSwitch-only preferences.

No skill code is executed. Imports are packaged for the Science management API.
"""
import base64
from contextlib import contextmanager
import hashlib
import http.client
import io
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sqlite3
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

REPO_DOWNLOAD_CHUNK = 1024 * 1024
REPO_DOWNLOAD_SECONDS = 600
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')
_spec = importlib.util.spec_from_file_location('local_skill_files', Path(__file__).with_name('science-local-skills.py'))
local_skill_files = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(local_skill_files)
MAX_BUNDLE = local_skill_files.MAX_BYTES
_agent_spec = importlib.util.spec_from_file_location('agent_skills', Path(__file__).with_name('agent-skills.py'))
agent_skills = importlib.util.module_from_spec(_agent_spec)
_agent_spec.loader.exec_module(agent_skills)


class WorkspaceError(Exception):
    pass


def regular(path):
    return not path.is_symlink() and path.is_file()


def read_json(path, default):
    if path.is_symlink():
        raise WorkspaceError('本地管理文件不能是符号链接。')
    if not path.exists():
        return default
    try:
        if path.stat().st_size > 2 * 1024 * 1024:
            raise WorkspaceError('本地管理文件过大。')
        return json.loads(path.read_text())
    except (ValueError, OSError):
        raise WorkspaceError('本地管理文件无法读取，请检查格式。') from None


def write_json(path, value):
    if path.is_symlink() or path.parent.is_symlink():
        raise WorkspaceError('本地管理目录不能是符号链接。')
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.workspace-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def metadata(text, directory):
    normalized = text.replace('\r\n', '\n')
    match = re.match(r'^---\s*\n(.*?)\n---(?:\n|$)', normalized, re.S)
    if not match:
        return None
    def field(key):
        m = re.search(r'^' + key + r':\s*(.*)$', match[1], re.M)
        if not m:
            return ''
        val = m[1].strip()
        if val in ('>', '|', '>-', '|-'):
            tail = match[1][m.end():]
            return ' '.join(line.strip() for line in tail.splitlines() if line.startswith((' ', '\t')))
        return val.strip('\"\'')
    return {'name': field('name') or directory, 'description': field('description')[:2000]}


def safe_zip(z, *, repository=False):
    files = z.infolist()
    # Repositories stay compressed on disk. Only bounded SKILL.md files and the
    # selected skill are read; unrelated large files are never decompressed.
    if not repository and (len(files) > local_skill_files.MAX_FILES or sum(f.file_size for f in files) > MAX_BUNDLE):
        raise WorkspaceError('仓库解压内容超过限制。')
    for f in files:
        p = PurePosixPath(f.filename)
        mode = f.external_attr >> 16
        if not agent_skills._platform.safe_archive_path(f.orig_filename) or p.is_absolute() or '..' in p.parts or '\\' in f.filename or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)) or f.flag_bits & 1:
            raise WorkspaceError('压缩包包含不安全的路径或符号链接。')
    return files


def bundle(files, name):
    if len(files) > local_skill_files.MAX_FILES or sum(len(content) for _, content in files) > MAX_BUNDLE:
        raise WorkspaceError(local_skill_files.LIMIT_MESSAGE)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel, content in files:
            z.writestr(name + '/' + rel, content)
    digest = hashlib.sha256()
    for rel, content in sorted(files):
        encoded = rel.encode('utf-8')
        digest.update(len(encoded).to_bytes(4, 'big') + encoded)
        digest.update(len(content).to_bytes(8, 'big') + content)
    return {'filename': name + '.zip', 'bundle': base64.b64encode(out.getvalue()).decode(),
            'fingerprint': digest.hexdigest()}


def message_blocks(content):
    blocks = content if isinstance(content, list) else [{'type': 'text', 'text': content}]
    result, remaining = [], 60000
    for block in blocks:
        if remaining <= 0: break
        if isinstance(block, str): block = {'type': 'text', 'text': block}
        if not isinstance(block, dict): continue
        kind = block.get('type', 'text')
        name = ''
        if kind in ('text', 'thinking'):
            text = block.get('text') or block.get('thinking') or ''
        elif kind == 'tool_use':
            name = str(block.get('name', '工具'))[:200]
            text = json.dumps(block.get('input', {}), ensure_ascii=False, indent=2)
        elif kind == 'tool_result':
            raw = block.get('content', '')
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2)
        else:
            kind, text = 'attachment', '[' + str(kind)[:100] + ']'
        if not isinstance(text, str): text = json.dumps(text, ensure_ascii=False)
        result.append({'type': kind, 'name': name, 'text': text[:remaining], 'truncated': len(text) > remaining,
                       'id': str(block.get('id') or '')[:200],
                       'tool_use_id': str(block.get('tool_use_id') or '')[:200],
                       'is_error': block.get('is_error') is True})
        remaining -= len(text)
    return result


class Workspace:
    def __init__(self, home, data, config):
        self.home, self.data, self.config = Path(home), Path(data), Path(config)
        self.pref = self.config / 'workspace.json'
        self.roots = agent_skills.roots(self.home)
        self.library = agent_skills.Library(self, WorkspaceError, read_json, write_json, metadata, local_skill_files)

    def preferences(self):
        value = read_json(self.pref, {})
        if not isinstance(value, dict):
            raise WorkspaceError('本地管理设置必须是 JSON 对象。')
        return value

    def databases(self):
        return [p for p in [self.data / 'operon-cli.db', *sorted(self.data.glob('orgs/*/operon-cli.db'))]
                if regular(p) and not p.parent.is_symlink()]

    @contextmanager
    def db(self, path):
        db = sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=2)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only = ON')
            yield db
        finally:
            db.close()

    def sessions(self):
        prefs = self.preferences().get('sessions', {})
        rows, warnings = [], []
        for path in self.databases():
            try:
                with self.db(path) as db:
                    columns = {r['name'] for r in db.execute('PRAGMA table_info(frames)')}
                    fields = ['f.id', 'f.project_id', 'f.created_at', 'f.updated_at']
                    for k in ['name', 'model', 'status', 'input_tokens', 'output_tokens', 'total_cost', 'is_hidden']:
                        fields.append('f.' + k if k in columns else 'NULL AS ' + k)
                    query = 'SELECT ' + ','.join(fields) + ', p.name AS project_name, (SELECT COUNT(*) FROM frame_messages m WHERE m.frame_id = f.id) AS messages, (SELECT msg_json FROM frame_messages m WHERE m.frame_id=f.id ORDER BY idx LIMIT 1) AS preview_json, (SELECT msg_json FROM frame_messages m WHERE m.frame_id=f.id ORDER BY idx DESC LIMIT 1) AS last_preview_json FROM frames f LEFT JOIN projects p ON f.project_id=p.id WHERE f.parent_frame_id IS NULL ORDER BY f.updated_at DESC LIMIT 5000'
                    for r in db.execute(query):
                        item = dict(r)
                        try:
                            preview = json.loads(item.pop('preview_json') or '{}')
                            item['preview'] = next((b['text'][:240] for b in message_blocks(preview.get('content', '')) if b['type'] == 'text'), '')
                        except (ValueError, AttributeError): item['preview'] = ''
                        try:
                            last = json.loads(item.pop('last_preview_json') or '{}')
                            item['last_preview'] = next((b['text'][:240] for b in message_blocks(last.get('content', '')) if b['type'] == 'text'), '')
                        except (ValueError, AttributeError): item['last_preview'] = ''
                        if item.pop('is_hidden', False):
                            continue
                        org = 'local' if path.parent == self.data else path.parent.name
                        key = org + ':' + item['id']
                        item.update(key=key, org=org)
                        item.update(prefs.get(key, {}))
                        item['title'] = item.get('title') or item.get('name') or '未命名会话'
                        rows.append(item)
            except sqlite3.Error:
                warnings.append('部分组织数据库暂不可读或版本不兼容，请关闭写入任务后刷新。')
        rows.sort(key=lambda r: r.get('updated_at') or 0, reverse=True)
        return {'sessions': rows, 'warnings': list(dict.fromkeys(warnings)), 'data_dir': str(self.data),
                'limited': len(rows) >= 5000}

    def session_path(self, key):
        if not isinstance(key, str) or ':' not in key:
            raise WorkspaceError('会话标识无效。')
        org, fid = key.split(':', 1)
        if not NAME.fullmatch(org) or not NAME.fullmatch(fid):
            raise WorkspaceError('会话标识无效。')
        path = self.data / 'operon-cli.db' if org == 'local' else self.data / 'orgs' / org / 'operon-cli.db'
        if path not in self.databases():
            raise WorkspaceError('会话所在数据库不存在。')
        return path, fid

    def transcript(self, key, offset=0):
        path, fid = self.session_path(key)
        offset = max(0, int(offset))
        with self.db(path) as db:
            frame = db.execute('SELECT id FROM frames WHERE id=? AND parent_frame_id IS NULL', (fid,)).fetchone()
            if not frame:
                raise WorkspaceError('会话已不存在。')
            count = db.execute('SELECT COUNT(*) FROM frame_messages WHERE frame_id=?', (fid,)).fetchone()[0]
            messages = []
            for row in db.execute('SELECT idx, msg_json FROM frame_messages WHERE frame_id=? ORDER BY idx LIMIT 100 OFFSET ?', (fid, offset)):
                try:
                    m = json.loads(row['msg_json'])
                    content = m.get('content', '')
                    if isinstance(content, list):
                        parts = []
                        for block in content:
                            if isinstance(block, str):
                                parts.append(block)
                            elif isinstance(block, dict):
                                t = block.get('type')
                                if t in ('text', 'thinking'):
                                    parts.append(block.get('text') or block.get('thinking') or '')
                                elif t == 'tool_use':
                                    parts.append('[工具调用：' + str(block.get('name', '')) + ']\n' + json.dumps(block.get('input', {}), ensure_ascii=False, indent=2))
                                elif t == 'tool_result':
                                    result = block.get('content', '')
                                    parts.append('[工具结果]\n' + (result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)))
                                else:
                                    parts.append('[' + str(t or '附件') + ']')
                        content = '\n\n'.join(parts)
                    elif not isinstance(content, str):
                        content = json.dumps(content, ensure_ascii=False)
                    messages.append({'idx': row['idx'], 'role': m.get('role', 'system'), 'text': content[:60000],
                                     'blocks': message_blocks(m.get('content', '')),
                                     'timestamp': m.get('timestamp') or m.get('created_at'), 'truncated': len(content) > 60000})
                except (ValueError, AttributeError):
                    messages.append({'idx': row['idx'], 'role': 'system', 'text': '[消息格式无法解析]'})
        return {'messages': messages, 'total': count, 'offset': offset, 'has_more': offset + len(messages) < count}

    def discover_local(self):
        return self.library.discover()['skills']

    def repos(self):
        return self.preferences().get('repos', [])

    def repo_path(self, key):
        return self.config / 'skill-repos' / (hashlib.sha256(key.encode()).hexdigest() + '.zip')

    def repo_add(self, p):
        repo = str(p.get('repo', '')).removeprefix('https://github.com/').rstrip('/').removesuffix('.git')
        branch = str(p.get('branch') or 'main')
        parts = repo.split('/')
        if len(parts) != 2 or not all(NAME.fullmatch(s) and s not in ('.', '..') for s in parts) or not NAME.fullmatch(branch) or branch in ('.', '..'):
            raise WorkspaceError('请填写 GitHub owner/repo 和有效分支名。')
        key = repo + '@' + branch
        url = 'https://codeload.github.com/' + repo + '/zip/refs/heads/' + branch
        path = self.repo_path(key)
        if path.parent.is_symlink() or path.is_symlink():
            raise WorkspaceError('仓库缓存不能是符号链接。')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            with tempfile.TemporaryDirectory(prefix='.repo-sync-', dir=path.parent) as temp:
                archive = Path(temp) / 'repository.zip'
                expires = time.monotonic() + REPO_DOWNLOAD_SECONDS
                with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'CSSwitch-Next'}), timeout=25) as response, archive.open('wb') as out:
                    while True:
                        if time.monotonic() >= expires:
                            raise WorkspaceError('仓库下载超时，请检查网络后重新同步。')
                        chunk = response.read(REPO_DOWNLOAD_CHUNK)
                        if not chunk:
                            break
                        out.write(chunk)
                with zipfile.ZipFile(archive) as z:
                    safe_zip(z, repository=True)
                # Close all handles before replacing the cache (required on Windows).
                os.replace(archive, path)
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            raise WorkspaceError('无法下载 GitHub 仓库，请检查网络、仓库和分支名。') from None
        except zipfile.BadZipFile:
            raise WorkspaceError('仓库压缩包不完整或格式无效，请重新同步。') from None
        except OSError:
            raise WorkspaceError('仓库缓存写入失败，请检查磁盘空间和目录权限后重试。') from None
        prefs = self.preferences()
        prefs['repos'] = [r for r in prefs.get('repos', []) if r['key'] != key] + [{'key': key, 'repo': repo, 'branch': branch}]
        write_json(self.pref, prefs)
        return {'message': '仓库已同步。'}

    def discover(self):
        local = self.library.discover()
        result, warnings = local['skills'], local['warnings']
        for repo in self.repos():
            path = self.repo_path(repo['key'])
            if not regular(path):
                warnings.append(repo['repo'] + ' 的缓存不可用，请重新同步。')
                continue
            try:
                with zipfile.ZipFile(path) as z:
                    for f in safe_zip(z, repository=True):
                        if not f.filename.endswith('/SKILL.md') or f.file_size > 256 * 1024:
                            continue
                        directory = str(PurePosixPath(f.filename).parent)
                        info = metadata(z.read(f).decode('utf8'), PurePosixPath(directory).name)
                        if info:
                            result.append({**info, 'key': 'repo:' + repo['key'] + ':' + directory, 'source': 'github', 'repo': repo['key'], 'directory': directory})
            except (zipfile.BadZipFile, UnicodeError):
                warnings.append(repo['repo'] + ' 的缓存格式不正确，请重新同步。')
        return {'skills': result, 'repos': self.repos(), 'warnings': warnings, 'sources': local['sources']}

    def skill_bundle(self, key):
        if isinstance(key, str) and key.startswith('library:'):
            name = key.removeprefix('library:')
            _, path = self.library.record(self.library.index(), name)
            files = self.library.tree(path)
            return {**bundle(list(files.items()), name), 'sourceKey': key, 'executable': self.library.executable_files(path, files)}
        item = next((s for s in self.discover()['skills'] if s['key'] == key or any(v['key'] == key for v in s.get('locations', []))), None)
        if not item:
            raise WorkspaceError('Skill 已不存在，请刷新。')
        if item.get('error'): raise WorkspaceError(item['error'])
        name = item['name'] if NAME.fullmatch(item['name']) else PurePosixPath(item['directory']).name
        if not NAME.fullmatch(name):
            raise WorkspaceError('Skill 目录名称无效。')
        files, total, executable = [], 0, []
        if item['source'] == 'github':
            with zipfile.ZipFile(self.repo_path(item['repo'])) as z:
                prefix = item['directory'] + '/'
                for f in safe_zip(z, repository=True):
                    if f.filename.startswith(prefix) and not f.is_dir():
                        rel = f.filename[len(prefix):]
                        # Match local source imports: do not copy repository
                        # metadata, hidden environment files or dependencies.
                        if any(p.startswith('.') or p in ('node_modules', '__pycache__', '__MACOSX') for p in PurePosixPath(rel).parts):
                            continue
                        total += f.file_size
                        if total > MAX_BUNDLE or len(files) >= local_skill_files.MAX_FILES:
                            raise WorkspaceError(local_skill_files.LIMIT_MESSAGE)
                        files.append((rel, z.read(f)))
                        if (f.external_attr >> 16) & 0o111: executable.append(rel)
        else:
            files = list(self.library.tree(Path(item['path']), source=True).items())
            executable = self.library.executable_files(Path(item['path']), dict(files))
        return {**bundle(files, name), 'sourceKey': key, 'executable': executable}

    def import_plan(self, p):
        """Split a multi-skill archive into individually validated native imports."""
        try:
            filename, encoded = p.get('filename'), p.get('bundle')
            if not isinstance(filename, str) or Path(filename).suffix.lower() not in ('.md', '.zip', '.skill'):
                raise WorkspaceError('请选择 Markdown、ZIP 或 .skill。')
            content = base64.b64decode(encoded, validate=True)
            if not content or len(content) > MAX_BUNDLE:
                raise WorkspaceError('导入文件不能超过 256 MB。')
            if Path(filename).suffix.lower() == '.md':
                name, _, _ = local_skill_files.unpack(filename, encoded)
                return {'skills': [{'name': name, 'filename': filename, 'bundle': encoded}]}
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                entries = safe_zip(archive)
                roots = sorted({PurePosixPath(f.filename).parent for f in entries
                                if not f.is_dir() and (f.filename.endswith('/SKILL.md') or f.filename == 'SKILL.md')},
                               key=lambda r: (len(r.parts), str(r)))
                roots = [r for i, r in enumerate(roots) if not any(r.is_relative_to(parent) for parent in roots[:i])]
                if not roots or len(roots) > 50:
                    raise WorkspaceError('压缩包需要包含 1–50 个 Skills。')
                skills = []
                for root in roots:
                    files = []
                    for entry in entries:
                        path = PurePosixPath(entry.filename)
                        if entry.is_dir() or not path.is_relative_to(root) or '__MACOSX' in path.parts or path.name == '.DS_Store' or path.name.startswith('._'):
                            continue
                        files.append((path.relative_to(root).as_posix(), archive.read(entry)))
                    pkg = bundle(files, 'skill')
                    name, _, _ = local_skill_files.unpack(pkg['filename'], pkg['bundle'])
                    skills.append({'name': name, 'filename': name + '.zip', 'bundle': pkg['bundle']})
                return {'skills': skills}
        except local_skill_files.LocalSkillError as error:
            raise WorkspaceError(str(error)) from None
        except (ValueError, TypeError, zipfile.BadZipFile, RuntimeError):
            raise WorkspaceError('技能包损坏或编码无效。') from None

    def run(self, operation, p):
        if operation == 'sessions_list': return self.sessions()
        if operation == 'session_messages': return self.transcript(p.get('key'), p.get('offset', 0))
        if operation == 'session_meta':
            path, fid = self.session_path(p.get('key'))
            with self.db(path) as db:
                if not db.execute('SELECT id FROM frames WHERE id=? AND parent_frame_id IS NULL', (fid,)).fetchone():
                    raise WorkspaceError('会话已不存在。')
            changes = p.get('changes', {})
            if not isinstance(changes, dict) or any(k not in ('title', 'starred', 'archived') for k in changes):
                raise WorkspaceError('不支持的会话设置。')
            for k, value in changes.items():
                if (k == 'title' and (not isinstance(value, str) or len(value) > 200)) or (k != 'title' and not isinstance(value, bool)):
                    raise WorkspaceError('会话设置无效。')
            prefs = self.preferences()
            prefs.setdefault('sessions', {}).setdefault(p['key'], {}).update(changes)
            write_json(self.pref, prefs)
            return {'message': '会话显示设置已保存。'}
        if operation.startswith(('skill_library_', 'agent_source')):
            return self.library.run(operation, p)
        if operation == 'skills_discover': return self.discover()
        if operation == 'skill_bundle': return self.skill_bundle(p.get('key'))
        if operation == 'skill_import_plan': return self.import_plan(p)
        if operation == 'repo_add': return self.repo_add(p)
        if operation == 'repo_remove':
            prefs = self.preferences()
            prefs['repos'] = [r for r in prefs.get('repos', []) if r['key'] != p.get('key')]
            write_json(self.pref, prefs)
            # Keep the inert cache until it is replaced by a later sync.
            return {'message': '仓库已移除，Science 中已安装的 Skills 保留。'}
        if operation == 'pricing_get': return {'pricing': self.preferences().get('pricing', {})}
        if operation == 'pricing_save':
            model, rates = p.get('model'), p.get('rates')
            if not isinstance(model, str) or not model.strip() or len(model) > 200 or not isinstance(rates, dict):
                raise WorkspaceError('模型定价格式无效。')
            if set(rates) != {'input', 'output', 'cache_read', 'cache_write'} or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 100000 for v in rates.values()):
                raise WorkspaceError('价格必须是每百万 Tokens 的非负美元金额。')
            prefs = self.preferences()
            prefs.setdefault('pricing', {})[model] = rates
            write_json(self.pref, prefs)
            return {'message': '模型定价已保存。'}
        raise WorkspaceError('不支持的本地管理操作。')


def main():
    timer = agent_skills._platform.deadline(55)
    try:
        data = json.loads(sys.stdin.read(local_skill_files.MAX_REQUEST))
        if data.get('operation') == 'repo_add':
            timer.cancel()
            # Let the download report its timeout and clean up the partial file
            # before the final cross-platform process deadline takes effect.
            timer = agent_skills._platform.deadline(REPO_DOWNLOAD_SECONDS + 60)
        value = Workspace(data['home'], data['data'], data['config']).run(data['operation'], data.get('params', {}))
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    except (WorkspaceError, local_skill_files.LocalSkillError) as e:
        print(json.dumps({'ok': False, 'message': str(e)}, ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok': False, 'message': '本地数据暂不可读或格式不兼容，请刷新并检查 Science 版本。'}, ensure_ascii=False))
    finally:
        timer.cancel()


if __name__ == '__main__': main()
