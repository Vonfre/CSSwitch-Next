"""Request-boundary subscription routing; never changes the live org or DB."""
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import signal
import time

from subscription_store import SubscriptionError, private_write
from subscription_platform import WINDOWS


def manager_for(store):
    spec = importlib.util.spec_from_file_location('csswitch_hot_manager', Path(__file__).with_name('science-subscriptions.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Manager(store.root.parent.parent)


def native_token(store, ident, account):
    # Live refresh stays with Science. Inactive refresh uses the same manager
    # lock and encrypted slots as the quota reader, never a second token copy.
    from subscription_usage import UsageService
    manager = manager_for(store)
    usage = UsageService(manager)
    credential, _ = usage.credential(account)
    expiry = credential.get('token_expires_at')
    if expiry and datetime.fromisoformat(expiry.replace('Z', '+00:00')).timestamp() <= time.time() + 60:
        if usage.native.active().get('account_uuid') == account['account_uuid']:
            usage.native.verify_login(account)
            credential, _ = usage.credential(account)
        else:
            credential = usage.refresh_inactive(ident, credential)
    token = credential.get('access_token')
    if not isinstance(token, str) or not token or any(c in token for c in '\r\n'):
        raise SubscriptionError('账号凭据无效，请重新授权。')
    return token


def stop_gateway(manager, interrupt=False):
    health = manager.health()
    if not health:
        return
    if health.get('active_requests'):
        if not interrupt:
            raise SubscriptionError('当前有请求正在回复，请完成后再更改接入模式，或使用「暂停并切换」。')
        pid = health.get('pid')
        if type(pid) is not int or pid <= 1:
            raise SubscriptionError('无法确认订阅网关进程，未强制停止。')
        command = manager.run('/bin/ps', ['-p', str(pid), '-o', 'command=']).stdout.strip()
        expected = str(Path(__file__).with_name('subscription_gateway.py')) + ' --root ' + str(manager.store.root)
        if not command.endswith(' ' + expected):
            raise SubscriptionError('无法确认订阅网关归属，未强制停止。')
        os.kill(pid, signal.SIGTERM)
    else:
        manager.gateway_request('/shutdown', post=True)
    for _ in range(100):
        if not manager.health():
            return
        time.sleep(.1)
    raise SubscriptionError('旧订阅服务尚未退出，暂停状态已保留。')


def configure(manager, enabled=True, pause_active=False):
    if WINDOWS:
        raise SubscriptionError('免重启切换当前仅在本机 macOS 版本验证，Windows 请使用原生切换。')
    from science_native_switch import NativeSwitcher
    native = NativeSwitcher(manager)
    state = manager.store.snapshot()
    before = manager.science_state()
    if enabled and state.get('hot_mode') and manager.owned(before):
        # Reusing a live connection must not turn an account selection into a
        # gateway upgrade that waits for the very reply the user wants to stop.
        if not manager.health():
            manager.ensure_gateway(allow_native=True)
        native.open_gui()
        return {'message': '免重启切换已连接。后续请求使用所选账号，当前请求继续完成。', 'same_process': True}
    pending = native.json_file(native.journal, {})
    if pending.get('phase') not in (None, 'complete', 'rolled_back'):
        raise SubscriptionError('请先恢复未完成的原生切换。')
    active = native.active()
    current = next((a for a in state['accounts'].values() if a['account_uuid'] == active.get('account_uuid')), None)
    if not current:
        raise SubscriptionError('请先完成 Science 官方登录并保存当前账号。')
    if enabled:
        if len(state['accounts']) < 2:
            raise SubscriptionError('请先保存至少两个账号。')
        if before.get('running'):
            native.verify_login(current)
    # This one-time connection change restarts the app; subsequent route()/auto
    # calls only atomically change selection and never touch the daemon or DB.
    health = manager.health()
    if health and health.get('active_requests') and not pause_active:
        raise SubscriptionError('当前有请求正在回复，请完成后再更改接入模式，或使用「暂停并切换」。')
    if pause_active and before.get('running'):
        from science_pause_switch import running_roots
        if running_roots(native):
            raise SubscriptionError('会话尚未保存暂停状态，未重启 Science。')
    native.close_gui()
    if manager.science_state().get('running'):
        manager.run(manager.science, ['stop'], timeout=90)
    if manager.science_state().get('running'):
        raise SubscriptionError('Science 尚未停止，未中断网关。')
    try:
        if enabled:
            # Upgrade an idle old gateway so it understands native login slots.
            stop_gateway(manager, interrupt=pause_active)
        with manager.store.locked():
            updated = manager.store.read()
            updated['hot_mode'] = enabled
            if enabled: updated['mode'] = 'auto'
            updated['revision'] += 1
            manager.store.write(updated)
        if enabled: manager.ensure_gateway(allow_native=True)
        launched = native.launch(hot=enabled)
        if enabled:
            private_write(manager.store.root / 'science.json', json.dumps(launched).encode())
            if not manager.owned(launched):
                raise SubscriptionError('Science 未接入订阅路由，未显示为免重启切换成功。')
        else:
            (manager.store.root / 'science.json').unlink(missing_ok=True)
        native.verify_login(current)
    except Exception:
        native.close_gui()
        if manager.science_state().get('running'):
            manager.run(manager.science, ['stop'], timeout=90)
        with manager.store.locked():
            restored = manager.store.read()
            restored['hot_mode'] = state.get('hot_mode', False)
            restored['mode'] = state['mode']
            restored['revision'] += 1
            manager.store.write(restored)
        previous_hot = bool(state.get('hot_mode'))
        if previous_hot: manager.ensure_gateway(allow_native=True)
        launched = native.launch(hot=previous_hot)
        if previous_hot: private_write(manager.store.root / 'science.json', json.dumps(launched).encode())
        raise SubscriptionError('接入模式未完成，已恢复原模式；登录与会话目录未改动。') from None
    return {'message': ('已启用免重启切换和自动接管。后续切换不会重启 Science，正在回复的请求保持原账号。'
                        if enabled else '已恢复官方直连。账号原生切换仍会重启 Science。'), 'same_process': False}
