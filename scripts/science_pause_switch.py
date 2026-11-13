"""Explicit stop-and-route handoff using Science's own resumable stop API.

Never edits frame state directly or automatically resubmits a model request.
Previously paused/failed conversations stay untouched, including safeguards stops.
"""
from contextlib import closing
import json
import re
import sqlite3
import time
import urllib.request

from subscription_platform import WINDOWS
from subscription_store import SubscriptionError


def running_roots(native):
    from science_history_shared import SharedHistory
    from science_native_switch import checked_uuid
    org = native.active().get('org_uuid')
    if not org:
        raise SubscriptionError('无法确认当前 Science 工作区，未暂停会话。')
    path = SharedHistory(native.data).resolve(checked_uuid(org)) / 'operon-cli.db'
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3)) as db:
        rows = db.execute("SELECT DISTINCT COALESCE(root_frame_id,id) FROM frames WHERE status IN ('processing','running','pending','queued')").fetchall()
    return [checked_uuid(row[0]) for row in rows]


def pause_running(native):
    roots = running_roots(native)
    if len(roots) > 1:
        raise SubscriptionError('有多个会话正在运行。请先在 Science 中暂停需要停止的会话，再切换账号；未停止其他任务。')
    if not roots:
        return []
    opener, origin = native.local_client()
    with opener.open(urllib.request.Request(origin + '/api/csrf', headers={'Origin': origin}), timeout=15) as response:
        response.read()
    jar = next(h.cookiejar for h in opener.handlers if isinstance(h, urllib.request.HTTPCookieProcessor))
    csrf = next((cookie.value for cookie in jar if cookie.name == 'operon_csrf'), '')
    if not csrf:
        raise SubscriptionError('无法验证 Science 暂停请求，未更改账号。')
    for root in roots:
        request = urllib.request.Request(origin + '/api/frames/' + root + '/cancel?reason=CSSwitch%20account%20handoff',
            data=b'{}', headers={'Origin': origin, 'Content-Type': 'application/json', 'x-operon-csrf': csrf})
        with opener.open(request, timeout=20) as response:
            result = json.load(response)
        if result.get('root_frame_id') != root:
            raise SubscriptionError('Science 尚未确认会话停止，请检查原会话；未更改账号。')
    # The API can acknowledge its in-memory stop before persisting frame state.
    deadline = time.monotonic() + 20
    while running_roots(native):
        if time.monotonic() >= deadline:
            raise SubscriptionError('Science 已收到停止请求，但仍在保存暂停状态。请稍后重试，未更改账号。')
        time.sleep(.2)
    return roots


def switch(manager, ident):
    if WINDOWS:
        raise SubscriptionError('暂停后切换当前仅适配 macOS Science 0.1.72。')
    from science_native_switch import NativeSwitcher
    from science_hot_switch import configure
    native = NativeSwitcher(manager)
    state = manager.store.snapshot()
    account = state['accounts'].get(ident)
    if not account or account.get('lapsed') or not account.get('enabled', True):
        raise SubscriptionError('请选择已授权且可用的账号，未暂停会话。')
    if account.get('cooldown_until', 0) > time.time():
        raise SubscriptionError('目标账号仍在额度冷却期，请选择其他账号，未暂停会话。')
    pending = native.json_file(native.journal, {})
    if pending.get('phase') not in (None, 'complete', 'rolled_back'):
        raise SubscriptionError('请先恢复未完成的账号切换，未暂停会话。')
    version = manager.run(manager.science, ['--version']).stdout
    if not re.search(r'\b0\.1\.72\b', version):
        raise SubscriptionError('当前 Science 版本尚未验证暂停接续，未更改会话。')
    if len(state['accounts']) < 2 or not any(a['account_uuid'] == native.active().get('account_uuid') for a in state['accounts'].values()):
        raise SubscriptionError('请先保存当前 Science 登录及备用账号，未暂停会话。')
    before = manager.science_state()
    paused = pause_running(native) if before.get('running') else []
    if state.get('hot_mode') and manager.owned(before) and manager.health():
        same_process = True
    else:
        # Only this explicit action may abandon orphaned gateway streams after
        # Science has saved the stop and exited. Normal mode changes still wait.
        same_process = configure(manager, enabled=True, pause_active=True)['same_process']
    manager.route(ident, prefer=True)
    after = manager.science_state()
    same_process = same_process and all(before.get(k) == after.get(k) for k in ('pid', 'started_at'))
    return {'message': '已保留会话状态并切换后续请求使用的账号。请回到原会话点击 Resume 继续；没有自动重发请求。',
            'same_process': same_process, 'paused_conversations': len(paused),
            'resume_required': True, 'account_id': ident}
