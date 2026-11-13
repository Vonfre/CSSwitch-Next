"""One physical Science workspace, handed off by rename between organizations.

Credentials stay in NativeSwitcher slots. Only enrolling an existing independent
workspace needs a staged merge; ordinary switches never snapshot history. The
caller stops Science and persists the publication plan before any rename/link.
Science requires its active org to be a real directory. Inactive aliases preserve
old absolute attachment/environment paths; they never become the active path.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import uuid

from subscription_platform import linked
from subscription_store import SubscriptionError, private_read, private_write


def org_id(value):
    try:
        if str(uuid.UUID(value)) == value:
            return value
    except (ValueError, TypeError, AttributeError):
        pass
    raise SubscriptionError('共享会话组织标识无效。')


def create_alias(target, path):
    if os.name == 'nt':
        # Junctions do not require Developer Mode or symlink privileges.
        import _winapi
        _winapi.CreateJunction(str(target), str(path))
    else:
        path.symlink_to(os.path.relpath(target, path.parent), target_is_directory=True)


def remove_alias(path):
    if os.name == 'nt' and not path.is_symlink():
        path.rmdir()  # Remove the junction itself, never its contents.
    else:
        path.unlink()


class SharedHistory:
    def __init__(self, data):
        self.data = Path(data)
        self.orgs = self.data / 'orgs'
        self.manifest = self.data / '.csswitch-history.json'
        if any(linked(p) for p in (self.orgs, self.data, *self.data.parents)):
            raise SubscriptionError('共享会话父目录不能是链接。')
        self.layout = self.read_layout()

    def read_layout(self):
        if linked(self.manifest):
            raise SubscriptionError('共享会话登记不能是链接。')
        if not self.manifest.exists():
            return None
        value = json.loads(private_read(self.manifest))
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('orgs'), list):
            raise SubscriptionError('共享会话登记格式不支持。')
        owner = org_id(value.get('owner'))
        anchor = org_id(value.get('anchor'))
        if owner not in value['orgs'] or anchor not in value['orgs'] or any(org_id(o) != o for o in value['orgs']):
            raise SubscriptionError('共享会话登记不完整。')
        return value

    def alias_matches(self, path, owner):
        if not linked(path):
            return False
        # Resolve only the immediate destination: aliases cannot form chains or
        # leave the org directory, even if a foreign link resolves back here.
        raw = os.readlink(path)
        if os.name == 'nt':
            raw = raw.removeprefix('\\\\?\\').removeprefix('\\??\\')
        destination = Path(os.path.abspath(path.parent / raw))
        return destination == self.orgs / owner

    def resolve(self, org):
        path = self.orgs / org_id(org)
        if self.layout and org in self.layout['orgs']:
            owner = self.layout['owner']
            canonical = self.orgs / owner
            if linked(canonical) or not canonical.is_dir():
                raise SubscriptionError('共享会话主目录缺失或被替换，请先恢复。')
            if org != owner and not self.alias_matches(path, owner):
                raise SubscriptionError('共享会话链接被修改，请先检查原目录。')
            return canonical
        if linked(path):
            raise SubscriptionError('拒绝未登记的会话目录链接。')
        return path

    def validate(self):
        if self.layout:
            for org in self.layout['orgs']:
                self.resolve(org)

    def prepare(self, source_org, target_org, backup, clone):
        from science_native_switch import tree_stamp, file_hash, verify_database
        from science_history_merge import merge_database, merge_files
        self.validate()
        source = self.resolve(source_org)
        target = self.resolve(target_org)
        canonical = self.orgs / self.layout['owner'] if self.layout else source
        paths = list(dict.fromkeys(p for p in (source, canonical, target) if p.exists()))
        stamps = {}
        for path in paths:
            for name in ('operon-cli.db', 'operon-cli.db-wal', 'operon-cli.db-shm'):
                if linked(path / name):
                    raise SubscriptionError('会话数据库或 WAL 不能是链接。')
            dbpath = path / 'operon-cli.db'
            if dbpath.exists():
                with closing(sqlite3.connect(dbpath, timeout=3)) as db:
                    if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
                        raise SubscriptionError('会话库仍被使用，未合并。')
                verify_database(dbpath)
            if len(paths) > 1:
                stamps[path.name] = tree_stamp(path)
        enrolled = list(dict.fromkeys([*(self.layout or {}).get('orgs', []), source_org, target_org]))
        plan = {'version': 1, 'old': self.layout,
                'new': {'version': 1, 'owner': target_org, 'orgs': enrolled,
                        'anchor': (self.layout or {}).get('anchor', source_org)},
                'source': source.name,
                'originals': [p.name for p in paths] if len(paths) > 1 else [],
                'stamps': stamps}
        counts = verify_database(source / 'operon-cli.db')
        if len(paths) > 1:
            stage = backup / 'history-next'
            clone(source, stage)
            if tree_stamp(stage) != stamps[source.name] or file_hash(stage / 'operon-cli.db') != file_hash(source / 'operon-cli.db'):
                raise SubscriptionError('共享会话暂存校验失败，原目录已保留。')
            for other in paths:
                if other != source:
                    merge_database(other / 'operon-cli.db', stage / 'operon-cli.db')
                    merge_files(other, stage, clone)
            counts = verify_database(stage / 'operon-cli.db')
            for path in paths:
                if tree_stamp(path) != stamps[path.name]:
                    raise SubscriptionError('合并期间会话发生变化，未发布。')
        moved = backup / 'history-next' if len(paths) > 1 else source
        plan['identity'] = self.identity(moved)
        return plan, counts

    @staticmethod
    def identity(path):
        if linked(path) or not path.is_dir():
            return None
        info = path.stat()
        return [info.st_dev, info.st_ino]

    def publish(self, plan, backup):
        from science_native_switch import tree_stamp
        self.validate()
        if self.read_layout() != plan['old']:
            raise SubscriptionError('共享会话登记发生变化，未发布。')
        new = plan['new']
        owner = new['owner']
        canonical = self.orgs / owner
        originals = plan['originals']
        old_orgs = (plan['old'] or {}).get('orgs', [])
        # Check all destinations before moving anything.
        for org in new['orgs']:
            path = self.orgs / org
            if org not in old_orgs and org not in originals and org != plan['source'] and os.path.lexists(path):
                raise SubscriptionError('目标会话在切换时出现，未覆盖。')
        for org in originals:
            if tree_stamp(self.orgs / org) != plan['stamps'][org]:
                raise SubscriptionError('会话在发布前发生变化，未覆盖。')
        moved = backup / 'history-next' if originals else self.orgs / plan['source']
        if self.identity(moved) != plan['identity']:
            raise SubscriptionError('共享会话目录被替换，未发布。')
        # The active org must be a real directory (Science rejects an active
        # symlink). Retarget all inactive aliases after the zero-copy handoff.
        if plan['old']:
            for org in old_orgs:
                if org != plan['old']['owner']:
                    remove_alias(self.orgs / org)
        for org in originals:
            (self.orgs / org).rename(backup / ('history-before-' + org))
        if moved != canonical:
            moved.rename(canonical)
        for org in new['orgs']:
            if org != owner:
                create_alias(canonical, self.orgs / org)
        private_write(self.manifest, json.dumps(new).encode())
        self.layout = new

    def rollback(self, plan, backup):
        """Idempotent rollback, including a crash between rename and journaling."""
        if plan.get('version') != 1:
            raise SubscriptionError('共享会话恢复格式不支持。')
        current = self.read_layout()
        if current not in (plan['old'], plan['new']):
            raise SubscriptionError('共享会话登记已被其他操作修改，未覆盖。')
        owner = org_id(plan['new']['owner'])
        source = self.orgs / org_id(plan['source'])
        canonical = self.orgs / owner
        old_owner = (plan['old'] or {}).get('owner')
        # A crash can leave either old aliases, new aliases, or a partial set.
        # Check every link before removing any; never traverse arbitrary links.
        aliases = []
        for org in plan['new']['orgs']:
            path = self.orgs / org_id(org)
            if linked(path):
                if not any(self.alias_matches(path, o) for o in (owner, old_owner) if o):
                    raise SubscriptionError('恢复时共享链接被修改，未覆盖。')
                aliases.append(path)
        for path in aliases:
            remove_alias(path)
        originals = [org_id(o) for o in plan['originals']]
        if originals:
            # Identity distinguishes the published stage from an original that
            # was already restored by an earlier, interrupted recovery attempt.
            if self.identity(canonical) == plan['identity']:
                if (backup / 'history-failed').exists():
                    raise SubscriptionError('共享会话恢复目录有冲突，未覆盖。')
                canonical.rename(backup / 'history-failed')
            for org in originals:
                saved = backup / ('history-before-' + org)
                path = self.orgs / org
                if saved.exists():
                    if os.path.lexists(path) or linked(saved):
                        raise SubscriptionError('原会话恢复位置已占用，未覆盖。')
                    saved.rename(path)
        elif source != canonical and self.identity(canonical) == plan['identity']:
            if os.path.lexists(source):
                raise SubscriptionError('原会话恢复位置已占用，未覆盖。')
            canonical.rename(source)
        elif self.identity(source) != plan['identity']:
            raise SubscriptionError('共享会话恢复源不匹配，未覆盖。')
        if plan['old'] is None:
            self.manifest.unlink(missing_ok=True)
        else:
            for org in plan['old']['orgs']:
                if org != old_owner:
                    path = self.orgs / org
                    if os.path.lexists(path):
                        raise SubscriptionError('原共享链接位置已占用，未覆盖。')
                    create_alias(self.orgs / old_owner, path)
            private_write(self.manifest, json.dumps(plan['old']).encode())
        self.layout = plan['old']
