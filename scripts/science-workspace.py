#!/usr/bin/env python3
"""Read Science databases without changing them; manage CSSwitch-only preferences.

No skill code is executed. Imports are packaged for the Science management API.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import signal
import sqlite3
import stat
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile

MAX_BUNDLE = 4 * 1024 * 1024
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


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


def safe_zip(z):
    files = z.infolist()
    if len(files) > 12000 or sum(f.file_size for f in files) > 64 * 1024 * 1024:
        raise WorkspaceError('仓库解压内容超过限制。')
    for f in files:
        p = PurePosixPath(f.filename)
        if p.is_absolute() or '..' in p.parts or '\\' in f.filename or stat.S_ISLNK(f.external_attr >> 16):
            raise WorkspaceError('压缩包包含不安全的路径或符号链接。')
    return files


def bundle(files, name):
    if len(files) > 1000 or sum(len(content) for _, content in files) > MAX_BUNDLE:
        raise WorkspaceError('Skill 超过 4 MB 或 1000 个文件，请缩小包内容。')
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        for rel, content in files:
            z.writestr(name + '/' + rel, content)
    return {'filename': name + '.zip', 'bundle': base64.b64encode(out.getvalue()).decode()}


class Workspace:
    def __init__(self, home, data, config):
        self.home, self.data, self.config = Path(home), Path(data), Path(config)
        self.pref = self.config / 'workspace.json'
        self.roots = {'codex': self.home / '.codex/skills', 'agents': self.home / '.agents/skills',
                      'claude': self.home / '.claude/skills'}

    def preferences(self):
        value = read_json(self.pref, {})
        if not isinstance(value, dict):
            raise WorkspaceError('本地管理设置必须是 JSON 对象。')
        return value

    def databases(self):
        return [p for p in sorted(self.data.glob('orgs/*/operon-cli.db'))
                if regular(p) and not p.parent.is_symlink()]

    def db(self, path):
        db = sqlite3.connect(path.absolute().as_uri() + '?mode=ro', uri=True, timeout=2)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only = ON')
        return db

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
                    query = 'SELECT ' + ','.join(fields) + ', p.name AS project_name, (SELECT COUNT(*) FROM frame_messages m WHERE m.frame_id = f.id) AS messages FROM frames f LEFT JOIN projects p ON f.project_id=p.id WHERE f.parent_frame_id IS NULL ORDER BY f.updated_at DESC LIMIT 5000'
                    for r in db.execute(query):
                        item = dict(r)
                        if item.pop('is_hidden', False):
                            continue
                        key = path.parent.name + ':' + item['id']
                        item.update(key=key, org=path.parent.name)
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
        path = self.data / 'orgs' / org / 'operon-cli.db'
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
                    messages.append({'idx': row['idx'], 'role': m.get('role', 'system'), 'text': content[:60000], 'truncated': len(content) > 60000})
                except (ValueError, AttributeError):
                    messages.append({'idx': row['idx'], 'role': 'system', 'text': '[消息格式无法解析]'})
        return {'messages': messages, 'total': count, 'offset': offset, 'has_more': offset + len(messages) < count}

    def discover_local(self):
        result = []
        for source, root in self.roots.items():
            if root.is_symlink() or not root.is_dir():
                continue
            for p in sorted(root.iterdir()):
                if p.name.startswith('.') or not p.is_dir() or p.is_symlink() or not regular(p / 'SKILL.md'):
                    continue
                try:
                    if (p / 'SKILL.md').stat().st_size > 256 * 1024:
                        continue
                    info = metadata((p / 'SKILL.md').read_text(), p.name)
                except (OSError, UnicodeError):
                    continue
                if info:
                    result.append({**info, 'key': source + ':' + p.name, 'source': source, 'directory': p.name, 'path': str(p)})
        return result

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
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'CSSwitch-Next'}), timeout=25) as response:
                content = response.read(16 * 1024 * 1024 + 1)
        except (urllib.error.URLError, TimeoutError):
            raise WorkspaceError('无法下载 GitHub 仓库，请检查网络、仓库和分支名。') from None
        if len(content) > 16 * 1024 * 1024:
            raise WorkspaceError('仓库压缩包超过 16 MB。')
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            safe_zip(z)
        path = self.repo_path(key)
        if path.parent.is_symlink() or path.is_symlink():
            raise WorkspaceError('仓库缓存不能是符号链接。')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(content)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp): os.unlink(temp)
        prefs = self.preferences()
        prefs['repos'] = [r for r in prefs.get('repos', []) if r['key'] != key] + [{'key': key, 'repo': repo, 'branch': branch}]
        write_json(self.pref, prefs)
        return {'message': '仓库已同步。'}

    def discover(self):
        result, warnings = self.discover_local(), []
        for repo in self.repos():
            path = self.repo_path(repo['key'])
            if not regular(path):
                warnings.append(repo['repo'] + ' 的缓存不可用，请重新同步。')
                continue
            try:
                with zipfile.ZipFile(path) as z:
                    for f in safe_zip(z):
                        if not f.filename.endswith('/SKILL.md') or f.file_size > 256 * 1024:
                            continue
                        directory = str(PurePosixPath(f.filename).parent)
                        info = metadata(z.read(f).decode('utf8'), PurePosixPath(directory).name)
                        if info:
                            result.append({**info, 'key': 'repo:' + repo['key'] + ':' + directory, 'source': 'github', 'repo': repo['key'], 'directory': directory})
            except (zipfile.BadZipFile, UnicodeError):
                warnings.append(repo['repo'] + ' 的缓存格式不正确，请重新同步。')
        return {'skills': result, 'repos': self.repos(), 'warnings': warnings}

    def skill_bundle(self, key):
        item = next((s for s in self.discover()['skills'] if s['key'] == key), None)
        if not item:
            raise WorkspaceError('Skill 已不存在，请刷新。')
        name = PurePosixPath(item['directory']).name
        if not NAME.fullmatch(name):
            raise WorkspaceError('Skill 目录名称无效。')
        files, total = [], 0
        if item['source'] == 'github':
            with zipfile.ZipFile(self.repo_path(item['repo'])) as z:
                prefix = item['directory'] + '/'
                for f in safe_zip(z):
                    if f.filename.startswith(prefix) and not f.is_dir():
                        total += f.file_size
                        if total > MAX_BUNDLE: raise WorkspaceError('Skill 超过 4 MB。')
                        files.append((f.filename[len(prefix):], z.read(f)))
        else:
            path = Path(item['path'])
            for base, dirs, names in os.walk(path, followlinks=False):
                dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(base) / d).is_symlink() and d not in ('node_modules', '__pycache__')]
                for filename in names:
                    p = Path(base) / filename
                    if filename.startswith('.') or not regular(p): continue
                    total += p.stat().st_size
                    if total > MAX_BUNDLE: raise WorkspaceError('Skill 超过 4 MB。')
                    files.append((p.relative_to(path).as_posix(), p.read_bytes()))
        return bundle(files, name)

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
        if operation == 'skills_discover': return self.discover()
        if operation == 'skill_bundle': return self.skill_bundle(p.get('key'))
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
    signal.alarm(55)
    try:
        data = json.loads(sys.stdin.read(1024 * 1024))
        value = Workspace(data['home'], data['data'], data['config']).run(data['operation'], data.get('params', {}))
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    except WorkspaceError as e:
        print(json.dumps({'ok': False, 'message': str(e)}, ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok': False, 'message': '本地数据暂不可读或格式不兼容，请刷新并检查 Science 版本。'}, ensure_ascii=False))


if __name__ == '__main__': main()
