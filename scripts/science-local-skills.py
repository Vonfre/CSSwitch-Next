#!/usr/bin/env python3
"""CSSwitch-owned local Science drafts. Never modify published or existing skills.

The authenticated bridge checks Science policy and verifies/attaches each draft
through Science's API. This module handles inert files only, without DB writes.
"""
import base64
import fcntl
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
from contextlib import contextmanager
import zipfile

MAX_BYTES = 4 * 1024 * 1024
MAX_FILES = 1000
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
UUID = re.compile(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z')
MARKER = '.csswitch-local.json'


class LocalSkillError(Exception):
    pass


def guarded(path):
    path = Path(path)
    if not path.is_absolute():
        raise LocalSkillError('Science 本地技能目录必须是绝对路径。')
    for component in reversed([path, *path.parents]):
        if component.is_symlink():
            raise LocalSkillError('Science 本地技能路径不能包含符号链接。')
    return path


def regular(path):
    return not path.is_symlink() and path.is_file()


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
        raise LocalSkillError('Skill 文件必须在 4 MB 以内。')
    if suffix == '.md':
        name, description = metadata(content)
        return name, description, {'SKILL.md': content}
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_FILES or sum(f.file_size for f in entries) > MAX_BYTES:
                raise LocalSkillError('Skill 解压内容不能超过 4 MB 或 1000 个文件。')
            for entry in entries:
                path = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (not entry.filename or path.is_absolute() or '..' in path.parts
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
        self.org_dir = guarded(data / 'orgs' / org)
        self.root = guarded(self.org_dir / 'skills')
        self.dbpath = guarded(self.org_dir / 'operon-cli.db')
        if not self.org_dir.is_dir() or not regular(self.dbpath):
            raise LocalSkillError('当前组织尚未就绪，请启动 Science 后刷新。')

    @contextmanager
    def locked(self):
        guarded(self.root).mkdir(mode=0o700, exist_ok=True)
        fd = os.open(self.root / '.csswitch-import.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
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
        return {'name': name, 'description': description, 'source': 'csswitch-local'}

    def disabled(self, user_id):
        try:
            with sqlite3.connect(self.dbpath.as_uri() + '?mode=ro', uri=True, timeout=2) as db:
                db.execute('PRAGMA query_only=ON')
                return {r[0] for r in db.execute("SELECT key FROM capability_settings WHERE user_id=? AND kind='skill' AND enabled=0", (user_id,))}
        except sqlite3.Error:
            raise LocalSkillError('Science 技能状态不可读，请检查 Science 版本后重试。') from None

    def install(self, name, files, reserved):
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
                for rel, content in sorted(files.items(), key=lambda item: item[0] == 'SKILL.md'):
                    path = guarded(dest / rel)
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with path.open('xb') as stream:
                        stream.write(content)
                (dest / MARKER).write_text(json.dumps({'owner': 'CSSwitch', 'name': name, 'version': 1}))
            except BaseException:
                shutil.rmtree(dest)
                raise

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
