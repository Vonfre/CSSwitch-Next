"""Discover inert local skills and own a private library with verified deployments.

Import copies originals. Only deployments recorded here can be changed or removed;
external skills, edited copies, and replaced links are never overwritten.
"""
from contextlib import contextmanager
from pathlib import Path
import importlib.util as _platform_import
_platform_spec = _platform_import.spec_from_file_location('csswitch_platform', Path(__file__).with_name('csswitch_platform.py'))
_platform = _platform_import.module_from_spec(_platform_spec)
_platform_spec.loader.exec_module(_platform)
import base64
import io
import zipfile
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
import uuid

SOURCES = [
    ('codex', 'Codex', '.codex/skills', True),
    ('agents', '共享 Agents', '.agents/skills', False),
    ('claude', 'Claude Code', '.claude/skills', True),
    ('gemini', 'Gemini CLI', '.gemini/skills', True),
    ('opencode', 'OpenCode', '.config/opencode/skills', True),
    ('cursor', 'Cursor', '.cursor/skills', True),
    ('windsurf', 'Windsurf', '.codeium/windsurf/skills', True),
    ('pi', 'Pi', '.pi/agent/skills', True),
    ('hermes', 'Hermes', '.hermes/skills', True),
    ('grok', 'Grok Build', '.grok/skills', True),
    ('minimax', 'MiniMax Code', '.minimax/skills', True),
    ('cc-switch', 'CC Switch', '.cc-switch/skills', False),
    ('codex-system', 'Codex 系统技能', '.codex/skills/.system', False),
]


def roots(home):
    result = {key: Path(home) / rel for key, _, rel, _ in SOURCES}
    if Path(home) == Path.home():
        for key, env, suffix in [('codex', 'CODEX_HOME', 'skills'), ('claude', 'CLAUDE_CONFIG_DIR', 'skills'),
                                 ('pi', 'PI_CODING_AGENT_DIR', 'skills'), ('minimax', 'MINIMAX_DATA_DIR', 'skills')]:
            value = os.environ.get(env)
            if value and Path(value).is_absolute():
                result[key] = Path(value) / suffix
        if os.environ.get('XDG_CONFIG_HOME', '').startswith('/'):
            result['opencode'] = Path(os.environ['XDG_CONFIG_HOME']) / 'opencode/skills'
    result['codex-system'] = result['codex'] / '.system'
    return result


class Library:
    def __init__(self, work, error, read_json, write_json, metadata, local):
        self.work, self.error = work, error
        self.read_json, self.write_json, self.metadata, self.local = read_json, write_json, metadata, local
        self.root = work.config / 'skills'
        self.index_path = work.config / 'skill-library.json'

    def sources(self):
        custom = self.work.preferences().get('agent_sources', [])
        result = [{'id': key, 'name': label, 'path': str(self.work.roots[key]), 'target': target}
                  for key, label, _, target in SOURCES]
        result += [{**item, 'target': True, 'custom': True} for item in custom]
        # Agent managers often keep their master copies in another ~/.../skills
        # directory. Discover those roots from installed links without walking HOME.
        known = {str(Path(s['path']).resolve()) for s in result}
        linked = {}
        for source in list(result):
            path = Path(source['path'])
            if not path.is_dir(): continue
            try: children = list(path.iterdir())
            except OSError: continue
            for child in children:
                if not child.is_symlink(): continue
                try:
                    parent = child.resolve(strict=True).parent
                    if parent.name not in ('skills', 'skill') or not parent.is_relative_to(self.work.home.resolve()) or str(parent) in known:
                        continue
                    key = 'linked-' + hashlib.sha256(str(parent).encode()).hexdigest()[:16]
                    linked[key] = {'id': key, 'name': parent.parent.name.lstrip('.') + ' 技能库', 'path': str(parent), 'target': False}
                except (OSError, RuntimeError): pass
        result += list(linked.values())
        for item in result:
            path = Path(item['path'])
            item['detected'] = path.is_dir() or path.parent.is_dir()
            item['exists'] = path.is_dir()
        return result

    def discover(self):
        sources, records, warnings = self.sources(), {}, []
        allowed = [Path(s['path']).resolve() for s in sources] + [self.root.resolve()]
        for source in sources:
            root = Path(source['path']); source.update(count=0, links=0)
            if not root.is_dir():
                continue
            try:
                children = sorted(root.iterdir())
            except OSError:
                warnings.append(source['name'] + ' 目录不可读。'); continue
            for child in children:
                if child.name.startswith('.') or not self.work_name(child.name):
                    continue
                try:
                    resolved = child.resolve(strict=True)
                    if not resolved.is_dir(): continue
                    if not any(resolved.is_relative_to(p) for p in allowed):
                        warnings.append(source['name'] + ' / ' + child.name + ' 链接指向未登记目录，可在本机目录中添加其来源。'); continue
                    skill = resolved / 'SKILL.md'
                    if skill.is_symlink() or not skill.is_file() or skill.stat().st_size > 256 * 1024: continue
                    info = self.metadata(skill.read_text(encoding='utf-8-sig'), child.name)
                    if not info: continue
                    source['count'] += 1; source['links'] += int(child.is_symlink())
                    key = source['id'] + ':' + child.name
                    location = {'source': source['id'], 'key': key, 'path': str(child), 'linked': child.is_symlink()}
                    physical = str(resolved)
                    if physical in records:
                        records[physical]['locations'].append(location)
                    else:
                        records[physical] = {**info, 'key': key, 'source': source['id'], 'directory': child.name,
                                             'path': str(resolved), 'locations': [location]}
                except (OSError, ValueError, RuntimeError, UnicodeError):
                    warnings.append(source['name'] + ' / ' + child.name + ' 无法读取，已跳过。')
        # Paused originals stay discoverable so a switch can restore them even
        # when this was their only active installation. Backups remain siblings:
        # relative symlinks therefore keep exactly the same resolution.
        try:
            paused = self.index().get('paused', {})
        except (OSError, self.error, self.local.LocalSkillError):
            paused = {}
            warnings.append('统一库记录暂不可读，仍显示可读取的本机技能。')
        for receipt in paused.values():
            source = next((s for s in sources if s['id'] == receipt['target']), None)
            if not source: continue
            source['pausedCount'] = source.get('pausedCount', 0) + 1
            key = source['id'] + ':' + receipt['directory']
            location = {'source': source['id'], 'key': key, 'path': str(Path(source['path']) / receipt['directory']),
                        'linked': receipt.get('link') is not None, 'disabled': True}
            try:
                _, backup = self.paused_paths(receipt)
                resolved = backup.resolve(strict=True)
                if not any(resolved.is_relative_to(p) for p in allowed):
                    raise self.error('停用链接指向未登记目录。')
                physical = str(resolved)
                if physical in records:
                    records[physical]['locations'].append(location)
                else:
                    records[physical] = {'name': receipt['name'], 'description': receipt.get('description', ''),
                                         'key': key, 'source': source['id'], 'directory': receipt['directory'],
                                         'path': str(resolved), 'locations': [location]}
            except (OSError, ValueError, RuntimeError, self.error, self.local.LocalSkillError):
                warnings.append(receipt['name'] + ' 的停用备份不可用，未修改原文件。')
                records['paused:' + key] = {'name': receipt['name'], 'description': receipt.get('description', ''),
                                           'key': key, 'source': source['id'], 'directory': receipt['directory'],
                                           'locations': [location], 'error': '停用备份不可用，请检查本机目录。'}
        return {'skills': list(records.values()), 'sources': sources, 'warnings': warnings}

    @staticmethod
    def work_name(name):
        import re
        return isinstance(name, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', name) is not None and _platform.safe_archive_path(name)

    def tree(self, path, source=False):
        files, total = {}, 0
        for base, dirs, names in os.walk(path, followlinks=False):
            for part in list(dirs):
                child = Path(base) / part
                if child.is_symlink() or getattr(child, 'is_junction', lambda: False)():
                    if not source: raise self.error('技能目录含外部链接，保留现有文件。')
                    dirs.remove(part)
                elif source and (part.startswith('.') or part in ('node_modules', '__pycache__')):
                    dirs.remove(part)
            for part in names:
                child = Path(base) / part
                if source and (part.startswith('.') or child.is_symlink()): continue
                fd = os.open(child, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode): raise self.error('技能包含特殊文件。')
                    total += info.st_size
                    if total > self.local.MAX_BYTES or len(files) >= self.local.MAX_FILES:
                        raise self.error(self.local.LIMIT_MESSAGE)
                    # The bundle limit is not a per-file read buffer. Reserving
                    # 256 MiB for every tiny icon is prohibitively slow on Windows.
                    # One extra byte still detects files growing after fstat().
                    data = stream.read(info.st_size + 1)
                    if len(data) != info.st_size: raise self.error('技能正在修改，请稍后重试。')
                    files[child.relative_to(path).as_posix()] = data
        if 'SKILL.md' not in files: raise self.error('技能缺少 SKILL.md。')
        return files

    def index(self):
        self.local.guarded(self.work.config)
        value = self.read_json(self.index_path, {'version': 1, 'skills': {}})
        if not isinstance(value, dict) or not isinstance(value.get('skills'), dict):
            raise self.error('统一技能库索引无法读取。')
        return value

    def save_index(self, index):
        if len(json.dumps(index, ensure_ascii=False).encode('utf-8')) > 2 * 1024 * 1024:
            raise self.error('技能管理记录超过容量限制，未修改文件。')
        self.write_json(self.index_path, index)

    @contextmanager
    def locked(self):
        self.local.guarded(self.work.config).mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.work.config / '.skill-library.lock', os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            _platform.lock_file(fd)
            yield
        finally:
            os.close(fd)

    def record(self, index, name):
        if not self.work_name(name) or name not in index['skills']:
            raise self.error('技能不在统一管理库中，请先导入。')
        record = index['skills'][name]
        path = self.local.guarded(self.root / name)
        return record, path

    def inspect(self, record, name, source):
        dest = Path(source['path']) / name
        receipt = record.get('deployments', {}).get(source['id'])
        if receipt and receipt.get('path') != str(dest):
            return {'status': 'conflict', 'message': '目录已改变，请恢复原目录后再管理'}
        exists = dest.exists() or dest.is_symlink()
        if not exists: return {'status': 'off'}
        if not receipt: return {'status': 'external', 'message': 'Agent 已有此技能，原文件独立管理'}
        try:
            self.local.guarded(dest.parent)
            if receipt['method'] == 'symlink':
                if dest.is_symlink() and dest.resolve() == (self.root / name).resolve():
                    return {'status': 'on', 'method': 'symlink'}
            elif not dest.is_symlink() and dest.is_dir() and self.local.fingerprint(self.tree(dest)) == receipt['fingerprint']:
                return {'status': 'on', 'method': 'copy'}
        except (OSError, ValueError, RuntimeError, self.error, self.local.LocalSkillError):
            pass
        return {'status': 'conflict', 'message': '文件或链接已改变，保留本地内容'}

    def listing(self):
        index = self.index(); sources = self.discover()['sources']; rows = []
        for name, record in sorted(index['skills'].items()):
            try:
                _, path = self.record(index, name)
                files = self.tree(path); current = self.local.fingerprint(files)
                row = {**record, 'name': name, 'key': 'library:' + name, 'source': 'library',
                       'locallyModified': current != record['fingerprint'], 'currentFingerprint': current}
                row['targets'] = {s['id']: self.inspect(record, name, s) for s in sources if s['target'] and (s['detected'] or s['id'] in record.get('deployments', {}))}
                rows.append(row)
            except (OSError, self.error, self.local.LocalSkillError) as exc:
                rows.append({'name': name, 'key': 'library:' + name, 'source': 'library', 'error': str(exc), 'targets': {}})
        return {'skills': rows, 'sources': sources, 'path': str(self.root)}

    def executable_files(self, root, files):
        return [rel for rel in files if (root / rel).stat().st_mode & 0o111]

    def write_tree(self, dest, files, executable=()):
        for rel, data in files.items():
            path = dest / rel; path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open('xb') as stream: stream.write(data)
            if rel in executable: path.chmod(0o700)

    def unpack(self, package):
        # Agent libraries allow valid directory names beyond Science's stricter
        # lowercase name schema. Science validates again when installing there.
        raw = base64.b64decode(package['bundle'], validate=True)
        if len(raw) > self.local.MAX_BYTES: raise self.error(self.local.LIMIT_MESSAGE)
        files = {}
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > self.local.MAX_FILES or sum(e.file_size for e in entries) > self.local.MAX_BYTES:
                raise self.error(self.local.LIMIT_MESSAGE)
            prefixes = set()
            for entry in entries:
                parts = entry.filename.split('/')
                if not _platform.safe_archive_path(entry.orig_filename) or len(parts) < 2 or any(not p or p in ('.', '..') or p.startswith('.') for p in parts) or '\\' in entry.filename:
                    raise self.error('技能包包含不安全路径。')
                prefixes.add(parts[0]); rel = '/'.join(parts[1:])
                mode = entry.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG): raise self.error('技能包包含特殊文件。')
                if rel in files: raise self.error('技能包包含重复路径。')
                files[rel] = archive.read(entry)
            if len(prefixes) != 1 or 'SKILL.md' not in files: raise self.error('请选择单个有效技能。')
        info = self.metadata(files['SKILL.md'].decode('utf-8-sig'), next(iter(prefixes)))
        if not info or not self.work_name(info['name']): raise self.error('技能名称不是有效目录名。')
        return info['name'], info['description'], files

    def import_skill(self, key, expected=None):
        package = self.work.skill_bundle(key)
        name, description, files = self.unpack(package)
        if expected is not None and name != expected: raise self.error('来源技能名称已变化，请刷新后重试。')
        digest = self.local.fingerprint(files)
        with self.locked():
            index = self.index()
            if name in index['skills']:
                _, path = self.record(index, name)
                if self.local.fingerprint(self.tree(path)) == digest:
                    return {'name': name, 'message': '相同内容已在统一库中，已跳过。'}
                raise self.error('统一库已有同名但内容不同的技能，未覆盖。')
            self.local.guarded(self.root).mkdir(parents=True, exist_ok=True, mode=0o700)
            path = self.local.guarded(self.root / name)
            try: path.mkdir(mode=0o700)
            except FileExistsError: raise self.error('技能目录已存在，未覆盖。') from None
            try:
                self.write_tree(path, files, package.get('executable', []))
                index['skills'][name] = {'description': description, 'sourceKey': key,
                                          'fingerprint': digest, 'deployments': {}}
                self.save_index(index)
            except BaseException:
                shutil.rmtree(path); raise
        return {'name': name, 'message': '已复制到统一技能库，原文件保留。'}

    def target(self, target):
        source = next((s for s in self.sources() if s['id'] == target and s['target']), None)
        if not source: raise self.error('未知的安装目标。')
        root = self.local.guarded(Path(source['path']))
        if root.resolve().is_relative_to(self.root.resolve()) or self.root.resolve().is_relative_to(root.resolve()):
            raise self.error('安装目标不能与统一技能库重叠。')
        return source, root

    def deploy(self, name, target, enabled, method='auto'):
        if method not in ('auto', 'symlink', 'copy') or not isinstance(enabled, bool):
            raise self.error('请选择自动、软链接或复制。')
        with self.locked():
            index = self.index(); record, origin = self.record(index, name)
            source, root = self.target(target); dest = root / name
            state = self.inspect(record, name, source)
            if state['status'] in ('external', 'conflict'): raise self.error(state['message'])
            if enabled:
                files = self.tree(origin)
                if state['status'] == 'on': return {'message': '此 Agent 已启用；停用后可更换同步方式。'}
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                actual = method
                if method in ('auto', 'symlink'):
                    try: dest.symlink_to(origin); actual = 'symlink'
                    except FileExistsError: raise self.error('目标目录已出现同名技能，未覆盖。') from None
                    except OSError:
                        if method == 'symlink': raise self.error('无法创建软链接，可改用复制。') from None
                        actual = 'copy'
                if actual == 'copy':
                    try: dest.mkdir(mode=0o700)
                    except FileExistsError: raise self.error('目标目录已出现同名技能，未覆盖。') from None
                    try: self.write_tree(dest, files, self.executable_files(origin, files))
                    except BaseException:
                        shutil.rmtree(dest); raise
                record.setdefault('deployments', {})[target] = {'method': actual, 'path': str(dest), 'fingerprint': self.local.fingerprint(files)}
                try: self.save_index(index)
                except BaseException:
                    if dest.is_symlink(): dest.unlink()
                    else: shutil.rmtree(dest)
                    raise
                return {'message': '已通过' + ('软链接' if actual == 'symlink' else '复制') + '启用。'}
            if state['status'] == 'off':
                record.setdefault('deployments', {}).pop(target, None); self.save_index(index)
                return {'message': '此 Agent 已停用。'}
            backup = root / ('.csswitch-remove-' + next(tempfile._get_candidate_names()))
            dest.rename(backup)
            try:
                record['deployments'].pop(target, None); self.save_index(index)
            except BaseException:
                backup.rename(dest); raise
            if backup.is_symlink(): backup.unlink()
            else: shutil.rmtree(backup)
            return {'message': '已停用此 Agent；统一库和原始技能保留。'}

    def paused_paths(self, receipt):
        _, root = self.target(receipt['target'])
        if str(root) != receipt['root'] or not self.work_name(receipt['directory']):
            raise self.error('Agent 目录已改变，保留停用备份。')
        import re
        if not re.fullmatch(r'\.csswitch-disabled-[a-f0-9]{32}', receipt['backup']):
            raise self.error('停用备份路径无效。')
        dest, backup = root / receipt['directory'], root / receipt['backup']
        info = backup.lstat()
        if [info.st_dev, info.st_ino] != receipt['identity']:
            raise self.error('停用备份已被替换，未恢复。')
        if receipt.get('link') is not None:
            if not backup.is_symlink() or os.readlink(backup) != receipt['link']:
                raise self.error('停用链接已改变，未恢复。')
        elif backup.is_symlink() or not backup.is_dir():
            raise self.error('停用备份类型已改变，未恢复。')
        return dest, backup

    def pause_originals(self, name, target, restore=False):
        with self.locked():
            _, root = self.target(target)
            index = self.index(); paused = index.setdefault('paused', {})
            moved = []
            try:
                if restore:
                    matches = [(key, value) for key, value in paused.items() if value['target'] == target and value['name'] == name]
                    if not matches: raise self.error('没有可恢复的停用技能。')
                    paths = [(key, *self.paused_paths(value)) for key, value in matches]
                    if len({str(dest) for _, dest, _ in paths}) != len(paths):
                        raise self.error('存在重复停用备份，未恢复。')
                    if any(dest.exists() or dest.is_symlink() for _, dest, _ in paths):
                        raise self.error('目标已出现同名目录，停用备份保留，未覆盖。')
                    for key, dest, backup in paths:
                        backup.rename(dest); moved.append((dest, backup)); del paused[key]
                else:
                    found = self.discover()['skills']
                    locations = [loc for skill in found if skill['name'] == name for loc in skill.get('locations', [])
                                 if loc['source'] == target and not loc.get('disabled')]
                    if not locations: return {'message': '此目标已停用。'}
                    for loc in locations:
                        dest = Path(loc['path'])
                        if dest.parent != root or not self.work_name(dest.name): raise self.error('技能目录无效。')
                        if any(r['target'] == target and r['directory'] == dest.name for r in paused.values()):
                            raise self.error('此目录已有停用备份，请先处理新出现的同名目录。')
                        if not dest.is_symlink() and any(s.get('path') == str(dest.resolve()) and any(other['path'] != str(dest) and not other.get('disabled') for other in s.get('locations', [])) for s in found):
                            raise self.error('其他 Agent 正通过软链接引用此目录，请先改为独立副本后再停用。')
                        token = uuid.uuid4().hex; backup = root / ('.csswitch-disabled-' + token)
                        info = dest.lstat()
                        item = next(skill for skill in found if skill['name'] == name and loc in skill.get('locations', []))
                        receipt = {'target': target, 'root': str(root), 'directory': dest.name, 'backup': backup.name,
                                   'name': name, 'description': item.get('description', ''),
                                   'identity': [info.st_dev, info.st_ino], 'link': os.readlink(dest) if dest.is_symlink() else None}
                        dest.rename(backup); moved.append((backup, dest)); paused[token] = receipt
                self.save_index(index)
            except BaseException:
                for current, original in reversed(moved): current.rename(original)
                raise
        return {'message': '已恢复原技能。' if restore else '已停用，原目录或链接已保留，可随时重新开启。'}

    def toggle(self, name, key, target, enabled, method='auto'):
        if not self.work_name(name) or not isinstance(enabled, bool): raise self.error('技能或开关参数无效。')
        self.target(target)
        index = self.index()
        record = index['skills'].get(name)
        source = next(s for s in self.sources() if s['id'] == target)
        state = self.inspect(record, name, source) if record else {'status': 'off'}
        if record and target in record.get('deployments', {}):
            if state['status'] == 'conflict': raise self.error(state['message'])
            if state['status'] == 'on': return self.deploy(name, target, enabled, method)
        if not enabled: return self.pause_originals(name, target)
        if any(r['target'] == target and r['name'] == name for r in index.get('paused', {}).values()):
            return self.pause_originals(name, target, restore=True)
        found = self.discover()['skills']
        if any(s['name'] == name and any(l['source'] == target and not l.get('disabled') for l in s.get('locations', [])) for s in found):
            return {'message': '目标已有此技能，保留原有内容。'}
        if key != 'library:' + name:
            self.import_skill(key, expected=name)
        return self.deploy(name, target, True, method)

    def remove(self, name):
        with self.locked():
            index = self.index(); record, path = self.record(index, name)
            if record.get('deployments'):
                raise self.error('请先停用此技能在各 Agent 的分发，再移出统一库。')
            self.tree(path)  # Never move a directory containing unexpected links.
            backup_root = self.local.guarded(self.work.config / 'skill-backups')
            backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = backup_root / (name + '-' + str(time.time_ns()))
            path.rename(backup)
            try:
                del index['skills'][name]; self.save_index(index)
            except BaseException:
                backup.rename(path); raise
            return {'message': '已移出统一库并保留本地备份。', 'backup': str(backup)}

    def update(self, name, expected):
        # Copy deployments must be disabled first: never silently leave stale copies.
        with self.locked():
            index = self.index(); record, path = self.record(index, name)
            current = self.local.fingerprint(self.tree(path))
            if current != expected or current != record['fingerprint']:
                raise self.error('统一库已有本地修改或内容已变化，未覆盖。')
            if any(r['method'] == 'copy' for r in record.get('deployments', {}).values()):
                raise self.error('请先停用复制方式的分发，更新后重新启用；软链接会自动跟随更新。')
            package = self.work.skill_bundle(record['sourceKey'])
            actual, description, files = self.unpack(package)
            if actual != name: raise self.error('来源技能名称已改变，未覆盖。')
            digest = self.local.fingerprint(files)
            if digest == current: return {'message': '与原来源一致，无需更新。'}
            stage = Path(tempfile.mkdtemp(prefix='.skill-stage-', dir=self.root))
            backup = self.root / ('.skill-backup-' + next(tempfile._get_candidate_names()))
            try:
                self.write_tree(stage, files, package.get('executable', [])); path.rename(backup)
                try:
                    stage.rename(path)
                    record.update(fingerprint=digest, description=description)
                    self.save_index(index)
                except BaseException:
                    if path.exists(): shutil.rmtree(path)
                    backup.rename(path); raise
                shutil.rmtree(backup)
            finally:
                if stage.exists(): shutil.rmtree(stage)
            return {'message': '已从原来源更新，软链接同步生效。'}

    def source_add(self, params):
        label, raw = params.get('name', ''), params.get('path', '')
        if not isinstance(label, str) or not label.strip() or len(label) > 60 or not isinstance(raw, str):
            raise self.error('请填写 Agent 名称和 Skills 目录。')
        path = Path(raw).expanduser()
        if not path.is_absolute() or not path.is_dir(): raise self.error('请选择已存在的绝对 Skills 目录。')
        path = path.resolve()
        if path == Path(path.anchor) or path == self.work.home or path.is_relative_to(self.work.config.resolve()) or self.root.resolve().is_relative_to(path):
            raise self.error('请选择具体的 Skills 目录。')
        if any(Path(s['path']).resolve() == path for s in self.sources()): raise self.error('该目录已在发现范围中。')
        item = {'id': 'custom-' + hashlib.sha256(str(path).encode()).hexdigest()[:16], 'name': label.strip(), 'path': str(path)}
        with self.locked():
            prefs = self.work.preferences(); prefs.setdefault('agent_sources', []).append(item)
            self.write_json(self.work.pref, prefs)
        return {'message': '已添加本机 Skills 目录。'}

    def source_remove(self, key):
        with self.locked():
            prefs = self.work.preferences()
            if not any(s['id'] == key for s in prefs.get('agent_sources', [])):
                raise self.error('只能移除手动添加的目录。')
            index = self.index()
            if any(r['target'] == key for r in index.get('paused', {}).values()):
                raise self.error('请先恢复该目录中已停用的原技能。')
            if any(key in s.get('deployments', {}) for s in index['skills'].values()):
                raise self.error('请先停用该目录中由 CSSwitch 创建的分发。')
            prefs['agent_sources'] = [s for s in prefs['agent_sources'] if s['id'] != key]
            self.write_json(self.work.pref, prefs)
        return {'message': '已移除发现目录，磁盘文件保留。'}

    def run(self, op, p):
        if op == 'agent_sources': return self.discover()
        if op == 'agent_source_add': return self.source_add(p)
        if op == 'agent_source_remove': return self.source_remove(p.get('id'))
        if op == 'skill_library_list': return self.listing()
        if op == 'skill_library_toggle': return self.toggle(p.get('name'), p.get('key'), p.get('target'), p.get('enabled'), p.get('method', 'auto'))
        if op == 'skill_library_preview':
            package = self.work.skill_bundle(p.get('key'))
            _, _, files = self.unpack(package)
            return {'content': files['SKILL.md'].decode('utf-8-sig')}
        if op == 'skill_library_content':
            _, path = self.record(self.index(), p.get('name'))
            return {'content': self.tree(path)['SKILL.md'].decode('utf-8-sig')}
        if op == 'skill_library_import': return self.import_skill(p.get('key'))
        if op == 'skill_library_deploy': return self.deploy(p.get('name'), p.get('target'), p.get('enabled'), p.get('method', 'auto'))
        if op == 'skill_library_remove': return self.remove(p.get('name'))
        if op == 'skill_library_update': return self.update(p.get('name'), p.get('expectedFingerprint'))
        raise self.error('未知的技能库操作。')
