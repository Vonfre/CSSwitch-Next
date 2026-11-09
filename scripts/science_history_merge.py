"""Merge stopped Science history snapshots without resetting resumable frames.

The caller stages both sides and publishes by rename only after verification.
This is deliberately stricter than an importer: divergent transcript entries
are never silently overwritten, and live process leases are not copied.
"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile

from subscription_store import SubscriptionError

SKIP = {'__drizzle_migrations', 'sqlite_sequence', 'sqlite_stat1', 'poller_lease',
        'session_claims', 'session_concurrency', 'compute_pending_terminate'}
# Account-specific secret records are not copied between login identities.
PRIVATE = {'oauth_tokens', 'anthropic_api_keys', 'cloud_credentials', 'user_secrets',
           'package_mirror_credentials', 'byoc_sandbox_handles', 'byoc_sandbox_copies', 'byoc_sandbox_copy_plans'}


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def archive_divergent_transcripts(src, dst, frames):
    """Keep the active transcript; expose the other snapshot as a native branch.

    Science 0.1.72 stores thin branch metadata in the context blob and the full
    message array in frame_branch_archives. Unknown layouts still fail closed.
    All writes target the unpublished staging database, never a running client.
    """
    preserved = {}
    for fid in sorted(frames):
        try:
            source_context = json.loads(src.execute("SELECT body FROM frame_blobs WHERE frame_id=? AND kind='context'", (fid,)).fetchone()[0])
            context = json.loads(dst.execute("SELECT body FROM frame_blobs WHERE frame_id=? AND kind='context'", (fid,)).fetchone()[0])
            meta = context['_branch_meta']
            source_meta = source_context['_branch_meta']
            assert isinstance(meta['branches'], dict) and meta['active_branch_id'] in meta['branches']
            assert source_meta['active_branch_id'] in source_meta['branches']
            messages = [json.loads(r[0]) for r in src.execute('SELECT msg_json FROM frame_messages WHERE frame_id=? ORDER BY idx', (fid,))]
            assert messages and all(isinstance(m, dict) for m in messages)
            payload = json.dumps(messages, ensure_ascii=False, separators=(',', ':'))
            # Science validates IDs with /^br_[0-9a-f]{8}$/.
            branch = 'br_' + hashlib.sha256(payload.encode()).hexdigest()[:8]
            existing = dst.execute('SELECT payload FROM frame_branch_archives WHERE frame_id=? AND branch_id=?', (fid, branch)).fetchone()
            if branch in meta['branches'] and not existing:
                raise ValueError('branch identity collision')
            if existing and json.loads(existing[0]) != messages:
                raise ValueError('branch collision')
            frame = dict(src.execute('SELECT * FROM frames WHERE id=?', (fid,)).fetchone())
            now = datetime.now(timezone.utc)
            entry = dict(source_meta['branches'][source_meta['active_branch_id']])
            entry.update(messages=None, parent_id=meta['active_branch_id'], fork_point=None,
                         created_at=now.isoformat(), updated_at=now.isoformat())
            # Mirror the native branch snapshot's resumable state fields.
            for key in ('tool_id_to_frame_id', 'sdk_child_spawn_tid', 'running_children',
                        'delegated_agents', 'pending_input_requests', 'checkpoint_inflight_start',
                        'checkpoint_rewinds', 'shadow_reviewer_frame_id', 'shadow_pinned_ask'):
                if '_' + key in source_context:
                    entry[key] = source_context['_' + key]
            for key in ('status', 'input_tokens', 'output_tokens', 'total_cost', 'cache_read_tokens',
                        'cache_write_tokens', 'aux_input_tokens', 'aux_output_tokens', 'aux_cost'):
                entry[key] = source_context.get('_' + key, frame.get(key))
            entry['plan_state'] = {k: v for k, v in source_context.items()
                                   if k.startswith('_plan_') or k in ('_step_statuses', '_fork_parity')}
            # Do not claim a complete child roster: imported children may also
            # belong to the current branch and must not be hidden on restore.
            entry.pop('child_roster_complete', None)
            for old_id, old_entry in source_meta['branches'].items():
                if old_id != source_meta['active_branch_id'] and src.execute(
                        'SELECT 1 FROM frame_branch_archives WHERE frame_id=? AND branch_id=?', (fid, old_id)).fetchone():
                    meta['branches'].setdefault(old_id, old_entry)
            meta['branches'].setdefault(branch, entry)
            dst.execute('INSERT OR IGNORE INTO frame_branch_archives (frame_id,branch_id,payload,updated_at) VALUES (?,?,?,?)',
                        (fid, branch, payload, int(now.timestamp() * 1000)))
            dst.execute("UPDATE frame_blobs SET body=? WHERE frame_id=? AND kind='context'",
                        (json.dumps(context, ensure_ascii=False), fid))
            preserved[fid] = (branch, messages)
        except (sqlite3.Error, TypeError, KeyError, ValueError, AssertionError):
            raise SubscriptionError('同一会话存在不同的消息分支，且当前版本不支持安全归档；两侧历史已保留。') from None
    return preserved


def merge_database(target, destination):
    if not target.exists():
        return {'added_rows': 0}
    with closing(sqlite3.connect(target.as_uri() + '?mode=ro&immutable=1', uri=True)) as src, closing(sqlite3.connect(destination)) as dst:
        src.row_factory = sqlite3.Row
        dst.row_factory = sqlite3.Row
        tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table'") if r[0] not in SKIP | PRIVATE and not r[0].startswith('sqlite_')]
        divergent = set()
        if 'frame_messages' in tables:
            existing = {(r[0], r[1]): r[2] for r in dst.execute('SELECT frame_id,idx,msg_json FROM frame_messages')}
            for row in src.execute('SELECT frame_id,idx,msg_json FROM frame_messages'):
                key = (row[0], row[1])
                if key in existing and existing[key] != row[2]:
                    divergent.add(row[0])
        if 'frame_branch_archives' in tables:
            existing_archives = {(r[0], r[1]): r[2] for r in dst.execute('SELECT frame_id,branch_id,payload FROM frame_branch_archives')}
            for fid, branch, payload in src.execute('SELECT frame_id,branch_id,payload FROM frame_branch_archives'):
                old = existing_archives.get((fid, branch))
                if old is not None and old != payload and json.loads(old) != json.loads(payload):
                    raise SubscriptionError('同一历史分支存在不同归档，已保留两侧备份，未覆盖。')
        schemas = {}
        for table in tables:
            columns = list(src.execute('PRAGMA table_info(' + quote(table) + ')'))
            other = list(dst.execute('PRAGMA table_info(' + quote(table) + ')'))
            if [tuple(r) for r in columns] != [tuple(r) for r in other]:
                raise SubscriptionError('两侧会话数据库格式不一致，未覆盖历史。')
            names = [r[1] for r in columns]
            keys = [r[1] for r in sorted(columns, key=lambda r: r[5]) if r[5]]
            if not keys:
                raise SubscriptionError('会话表缺少唯一标识，未自动合并。')
            schemas[table] = (names, keys)
        # Keep the active snapshot on equal timestamps; include newer updates
        # from the other side and every missing row under its original ID.
        before = dst.total_changes
        dst.execute('PRAGMA foreign_keys=OFF')
        with dst:
            archived = archive_divergent_transcripts(src, dst, divergent)
            for table, (names, keys) in schemas.items():
                columns = ','.join(map(quote, names))
                update = [name for name in names if name not in keys]
                # Seeded agents/settings can have different IDs but the same
                # secondary unique name. Keep the active setting in that case;
                # the history inclusion and FK checks below forbid data loss.
                sql = f'INSERT OR IGNORE INTO {quote(table)} ({columns}) VALUES ({",".join("?" for _ in names)}) ON CONFLICT ({",".join(map(quote, keys))}) '
                if 'updated_at' in names and update:
                    sql += 'DO UPDATE SET ' + ','.join(f'{quote(n)}=excluded.{quote(n)}' for n in update)
                    sql += f' WHERE excluded.updated_at > {quote(table)}.updated_at'
                else:
                    sql += 'DO NOTHING'
                try:
                    def rows():
                        for row in src.execute('SELECT ' + columns + ' FROM ' + quote(table)):
                            # Preserve the active branch's matching execution
                            # state; never combine two transcripts by index.
                            if table == 'frames' and row['id'] in divergent:
                                continue
                            if table in ('frame_messages', 'frame_blobs', 'frame_system_prompts') and row['frame_id'] in divergent:
                                continue
                            yield tuple(row)
                    dst.executemany(sql, rows())
                except sqlite3.IntegrityError as error:
                    raise SubscriptionError('历史记录的唯一标识有冲突（' + table + ': ' + str(error) + '），未覆盖两侧会话。') from None
            if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or dst.execute('PRAGMA foreign_key_check').fetchall():
                raise SubscriptionError('合并后的会话关联校验失败，原历史已保留。')
            # Verify complete transcript inclusion, not just counts.
            for table in ('frames', 'projects', 'frame_messages', 'frame_blobs', 'artifacts', 'artifact_versions', 'artifact_dependencies', 'execution_log', 'cell_source_log', 'directory_attachments', 'frame_system_prompts', 'frame_branch_archives'):
                if table not in schemas:
                    continue
                names, keys = schemas[table]
                check_columns = keys + (['msg_json'] if table == 'frame_messages' else [])
                projection = ','.join(map(quote, check_columns))
                actual = {tuple(row) for row in dst.execute(f'SELECT {projection} FROM {quote(table)}')}
                expected = {tuple(row) for row in src.execute(f'SELECT {projection} FROM {quote(table)}')
                            if not (table == 'frame_messages' and row['frame_id'] in divergent)}
                if not expected <= actual:
                    raise SubscriptionError('合并后有会话或消息未保留，未发布切换。')
            for fid, (branch, messages) in archived.items():
                row = dst.execute('SELECT payload FROM frame_branch_archives WHERE frame_id=? AND branch_id=?', (fid, branch)).fetchone()
                context = json.loads(dst.execute("SELECT body FROM frame_blobs WHERE frame_id=? AND kind='context'", (fid,)).fetchone()[0])
                if not row or json.loads(row[0]) != messages or branch not in context['_branch_meta']['branches']:
                    raise SubscriptionError('历史分支归档校验失败，未发布切换。')
        return {'added_rows': dst.total_changes - before, 'archived_branches': len(archived)}


def merge_files(source, destination, clone):
    """Fill missing paths without following links; retain differing file copies."""
    skip = {'operon-cli.db', 'operon-cli.db-wal', 'operon-cli.db-shm', '.csswitch-merge-conflicts'}
    conflicts = []
    conflict_root = None
    def copy(src, dst):
        if src.is_symlink():
            dst.symlink_to(os.readlink(src), target_is_directory=src.is_dir())
        elif src.is_dir():
            clone(src, dst)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
    def visit(src, dst, relative):
        nonlocal conflict_root
        if not os.path.lexists(dst):
            copy(src, dst)
            return
        a, b = src.lstat(), dst.lstat()
        if stat.S_ISDIR(a.st_mode) and stat.S_ISDIR(b.st_mode):
            for child in src.iterdir():
                visit(child, dst / child.name, relative / child.name)
            return
        if stat.S_ISLNK(a.st_mode) and stat.S_ISLNK(b.st_mode) and os.readlink(src) == os.readlink(dst):
            return
        if stat.S_ISREG(a.st_mode) and stat.S_ISREG(b.st_mode):
            if a.st_size == b.st_size:
                def digest(p):
                    h = hashlib.sha256()
                    with p.open('rb') as f:
                        for block in iter(lambda: f.read(1024 * 1024), b''): h.update(block)
                    return h.digest()
                if digest(src) == digest(dst): return
        # The active workspace wins, but retain the other version in the merged
        # snapshot as well as in the immutable pre-switch backup.
        if conflict_root is None:
            conflict_root = Path(tempfile.mkdtemp(prefix='.csswitch-merge-conflicts-', dir=destination))
        conflict = conflict_root / relative
        conflict.parent.mkdir(parents=True, exist_ok=True)
        if not os.path.lexists(conflict): copy(src, conflict)
        conflicts.append(str(relative))
    for child in source.iterdir():
        if child.name not in skip and not child.name.startswith('.csswitch-merge-conflicts-'):
            visit(child, destination / child.name, Path(child.name))
    return {'file_conflicts_preserved': len(conflicts)}
