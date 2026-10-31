#!/usr/bin/env python3
"""CSSwitch-owned local Science drafts. Never modify published or existing skills.

The authenticated bridge checks Science policy and verifies/attaches each draft
through Science's API. This module handles inert files only, without DB writes.
"""
from pathlib import Path
import base64
import importlib.util as _platform_import
_platform_spec = _platform_import.spec_from_file_location('csswitch_platform', Path(__file__).with_name('csswitch_platform.py'))
_platform = _platform_import.module_from_spec(_platform_spec)
_platform_spec.loader.exec_module(_platform)
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import contextmanager, closing
import zipfile

# Shared by repository packaging, the library and the Science bridge. Large
# template/icon libraries must survive every stage, including installed reads.
MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 50000
MAX_REQUEST = ((MAX_BYTES + 2) // 3) * 4 + 16 * 1024 * 1024
LIMIT_MESSAGE = 'Skill 不能超过 256 MB 或 50000 个文件。'
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')
MARKER = '.csswitch-local.json'


class LocalSkillError(Exception):
    pass


def fingerprint(files):
    digest = hashlib.sha256()
    for name, content in sorted(files.items()):
        encoded = name.encode('utf-8')
        digest.update(len(encoded).to_bytes(4, 'big') + encoded)
        digest.update(len(content).to_bytes(8, 'big') + content)
    return digest.hexdigest()


def source_key(value):
    if value is not None and (not isinstance(value, str) or len(value) > 1000
                              or re.search(r'[\x00-\x1f]', value)
                              or not (value.startswith(('codex:', 'agents:', 'claude:', 'repo:', 'library:', 'gemini:', 'opencode:', 'cursor:', 'windsurf:', 'pi:', 'hermes:', 'grok:', 'minimax:', 'cc-switch:', 'codex-system:')) or re.match(r'^(?:custom|linked)-[a-f0-9]{16}:', value))):
        raise LocalSkillError('技能更新来源无效。')
    return value


def guarded(path):
    path = Path(path)
    if not path.is_absolute():
        raise LocalSkillError('Science 本地技能目录必须是绝对路径。')
    for component in reversed([path, *path.parents]):
        if component.is_symlink() or getattr(component, 'is_junction', lambda: False)():
            raise LocalSkillError('Science 本地技能路径不能包含符号链接。')
    return path


def regular(path):
    return not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)() and path.is_file()


def metadata(content):
    try:
        text = content.decode('utf-8-sig').replace('\r\n', '\n')
    except UnicodeError:
        raise LocalSkillError('SKILL.md 必须使用 UTF-8 编码。') from None
    if len(content) > 256 * 1024:
        raise LocalSkillError('SKILL.md 不能超过 256 KB。')
    match = re.match(r'^---[ \t]*\n(.*?)\n---[ \t]*(?:\n|$)', text, re.S)
    if not match:
        raise LocalSkillError('SKILL.md 需要包含 name 和 description 的 YAML 文件头。')
    def field(key):
        rows = re.findall(r'^' + key + r':[ \t]*(.*)$', match[1], re.M)
        if len(rows) != 1:
            raise LocalSkillError('SKILL.md 需要唯一的 ' + key + ' 字段。')
        value = rows[0].strip()
        if value in ('|', '>', '|-', '>-', '|+', '>+'):
            tail = match[1].split(key + ':', 1)[1].split('\n', 1)[-1]
            lines = []
            for line in tail.splitlines():
                if line and not line.startswith((' ', '\t')):
                    break
                lines.append(line.strip())
            return ' '.join(lines).strip()
        return value.strip('"\'')
    name, description = field('name'), field('description')
    if not NAME.fullmatch(name):
        raise LocalSkillError('Skill name 请使用 1–64 位小写字母、数字或短横线。')
    if not description:
        raise LocalSkillError('SKILL.md 的 description 不能为空。')
    return name, description[:2000]


def unpack(filename, encoded):
    if not isinstance(filename, str) or len(filename) > 160 or re.search(r'[/\\\x00-\x1f";]', filename):
        raise LocalSkillError('Skill 文件名无效。')
    suffix = Path(filename).suffix.lower()
    if suffix not in ('.md', '.zip', '.skill'):
        raise LocalSkillError('请选择 ZIP、.skill 或 Markdown Skill 文件。')
    try:
        content = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise LocalSkillError('Skill 文件编码无效。') from None
    if not content or len(content) > MAX_BYTES:
        raise LocalSkillError('Skill 文件必须在 256 MB 以内。')
    if suffix == '.md':
        name, description = metadata(content)
        return name, description, {'SKILL.md': content}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(f.file_size for f in entries) > MAX_BYTES:
                raise LocalSkillError(LIMIT_MESSAGE)
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (not _platform.safe_archive_path(entry.filename) or path.is_absolute() or '..' in path.parts
                        or '\\' in entry.filename or re.search(r'[\x00-\x1f]', entry.filename)
                        or (mode and stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR))
                        or entry.flag_bits & 1):
                    raise LocalSkillError('压缩包包含不安全路径、特殊文件或加密内容。')
            roots = [PurePosixPath(f.filename).parent for f in entries
                     if not f.is_dir() and PurePosixPath(f.filename).name == 'SKILL.md']
            if not roots:
                raise LocalSkillError('压缩包中找不到 SKILL.md。')
            root = min(roots, key=lambda p: len(p.parts))
            if any(not p.is_relative_to(root) for p in roots):
                raise LocalSkillError('一次只能导入一个 Skill，请选择单个技能包。')
            files = {}
            seen = set()
            for entry in entries:
                if entry.is_dir():
                    continue
                path = PurePosixPath(entry.filename)
                if '__MACOSX' in path.parts or path.name == '.DS_Store' or path.name.startswith('._'):
                    continue
                if not path.is_relative_to(root):
                    raise LocalSkillError('技能包包含技能目录以外的文件。')
                rel = path.relative_to(root)
                if any(p.startswith('.') for p in rel.parts):
                    raise LocalSkillError('技能包不能包含隐藏文件或目录。')
                if str(rel).casefold() in seen:
                    raise LocalSkillError('技能包包含重复路径。')
                seen.add(str(rel).casefold())
                files[str(rel)] = archive.read(entry)
            name, description = metadata(files['SKILL.md'])
            return name, description, files
    except (zipfile.BadZipFile, KeyError, RuntimeError, OSError, ValueError):
        raise LocalSkillError('Skill 压缩包损坏或格式不兼容。') from None


class LocalSkills:
    def __init__(self, data_dir, auth_dir):
        data, auth = guarded(data_dir), guarded(auth_dir)
        identity = auth / 'active-org.json'
        if not regular(identity) or identity.stat().st_size > 4096:
            raise LocalSkillError('无法确定当前 Science 组织，请重新启动第三方 Science。')
        try:
            org = json.loads(identity.read_text()).get('org_uuid')
        except (ValueError, OSError):
            raise LocalSkillError('Science 当前组织设置不可读。') from None
        if not isinstance(org, str) or not UUID.fullmatch(org):
            raise LocalSkillError('Science 当前组织标识无效。')
        self.org_dir = guarded(data if regular(data / 'operon-cli.db') else data / 'orgs' / org)
        self.root = guarded(self.org_dir / 'skills')
        self.dbpath = guarded(self.org_dir / 'operon-cli.db')
        if not self.org_dir.is_dir() or not regular(self.dbpath):
            raise LocalSkillError('当前组织尚未就绪，请启动 Science 后刷新。')

    @contextmanager
    def locked(self):
        guarded(self.root).mkdir(mode=0o700, exist_ok=True)
        fd = os.open(self.root / '.csswitch-import.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        try:
            _platform.lock_file(fd)
            yield
        finally:
            os.close(fd)

    def info(self, name):
        if not NAME.fullmatch(name):
            return None
        path = guarded(self.root / name)
        marker = path / MARKER
        if not path.is_dir() or not regular(marker) or marker.stat().st_size > 4096:
            return None
        if any((path / p).exists() or (path / p).is_symlink() for p in ('.catalog_stamp', '.sync-org', '.plugin', '.import-source.json')):
            return None
        if not regular(path / 'SKILL.md'):
            return None
        try:
            value = json.loads(marker.read_text())
            actual, description = metadata((path / 'SKILL.md').read_bytes())
        except (ValueError, OSError, LocalSkillError):
            return None
        if value.get('owner') != 'CSSwitch' or value.get('name') != name or actual != name:
            return None
        files = self.files(path)
        current = fingerprint(files)
        return {'name': name, 'description': description, 'source': 'csswitch-local',
                'sourceKey': value.get('sourceKey'), 'fingerprint': current,
                'locallyModified': bool(value.get('fingerprint') and current != value['fingerprint'])}

    def files(self, path):
        files, total = {}, 0
        for base, dirs, names in os.walk(path, followlinks=False):
            for part in dirs + names:
                child = Path(base) / part
                if child.is_symlink() or getattr(child, 'is_junction', lambda: False)():
                    raise LocalSkillError('技能目录含符号链接，请先检查目录内容。')
            for part in names:
                file = Path(base) / part
                if file.name == MARKER and file.parent == path:
                    continue
                if not regular(file):
                    raise LocalSkillError('技能目录包含特殊文件。')
                total += file.stat().st_size
                if total > MAX_BYTES or len(files) >= MAX_FILES:
                    raise LocalSkillError('已安装技能超过管理限制，请先检查目录。')
                files[file.relative_to(path).as_posix()] = file.read_bytes()
        return files

    def write_files(self, dest, name, files, origin=None):
        for rel, content in sorted(files.items(), key=lambda item: item[0] == 'SKILL.md'):
            path = guarded(dest / rel)
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(content)
        (dest / MARKER).write_text(json.dumps({'owner': 'CSSwitch', 'name': name,
            'version': 2, 'fingerprint': fingerprint(files), 'sourceKey': source_key(origin)}))

    def disabled(self, user_id):
        try:
            with closing(sqlite3.connect(self.dbpath.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                db.execute('PRAGMA query_only=ON')
                return {r[0] for r in db.execute("SELECT key FROM capability_settings WHERE user_id=? AND kind='skill' AND enabled=0", (user_id,))}
        except sqlite3.Error:
            raise LocalSkillError('Science 技能状态不可读，请检查 Science 版本后重试。') from None

    def install(self, name, files, reserved, origin=None):
        if not NAME.fullmatch(name):
            raise LocalSkillError('Skill 名称无效。')
        aliases = {name, name.removeprefix('operon-'), 'operon-' + name}
        if aliases.intersection(reserved):
            raise LocalSkillError('此名称与已安装或 Science 内置技能冲突，请修改 Skill name。')
        with self.locked():
            dest = guarded(self.root / name)
            try:
                dest.mkdir(mode=0o700)  # Exclusive reservation; never replace any existing directory.
            except FileExistsError:
                raise LocalSkillError('同名 Skill 已存在，未覆盖任何文件。') from None
            try:
                self.write_files(dest, name, files, origin)
            except BaseException:
                shutil.rmtree(dest)
                raise

    @contextmanager
    def replacing(self, name, files, expected, origin=None):
        """Keep the complete old draft until Science verifies the replacement."""
        with self.locked():
            previous = self.info(name)
            if not previous:
                raise LocalSkillError('仅可更新 CSSwitch 管理的本地技能。')
            if previous['locallyModified']:
                raise LocalSkillError('技能已有本地修改，已跳过更新以保留你的内容。')
            if not expected or previous['fingerprint'] != expected:
                raise LocalSkillError('技能内容已变化，请重新检查更新。')
            origin = source_key(origin)
            if previous['sourceKey'] and origin != previous['sourceKey']:
                raise LocalSkillError('更新来源与安装来源不一致，请使用原来源。')
            if metadata(files['SKILL.md'])[0] != name:
                raise LocalSkillError('新技能 name 与已安装名称不一致。')
            dest = guarded(self.root / name)
            stage = Path(tempfile.mkdtemp(prefix='.csswitch-stage-', dir=self.root))
            backup = Path(tempfile.mkdtemp(prefix='.csswitch-backup-', dir=self.root))
            moved, committed = False, False
            try:
                self.write_files(stage, name, files, origin)
                backup.rmdir()
                os.replace(dest, backup)
                moved = True
                os.replace(stage, dest)
                yield
                committed = True
            except BaseException:
                if moved:
                    if dest.exists():
                        shutil.rmtree(dest)
                    os.replace(backup, dest)
                raise
            finally:
                if stage.exists(): shutil.rmtree(stage)
                # Keep the backup if rollback itself fails (permissions/I/O).
                if committed and backup.exists(): shutil.rmtree(backup)

    def remove(self, name):
        with self.locked():
            if not self.info(name):
                raise LocalSkillError('只可移除 CSSwitch 安装的本地技能，未修改其他技能。')
            path = guarded(self.root / name)
            # Reject symlinks anywhere before recursive removal.
            for base, dirs, files in os.walk(path, followlinks=False):
                for part in dirs + files:
                    if (Path(base) / part).is_symlink():
                        raise LocalSkillError('技能目录含符号链接，请先检查目录内容。')
            shutil.rmtree(path)
