#!/usr/bin/env python3
"""CSSwitch subscriptions, native Science login switching and local session handoff."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from subscription_platform import WINDOWS, linked, protect, lock_file, subprocess_options
from science_paths import resolve
try:
    from subscription_store import Store, SubscriptionError, private_read, private_write
except ModuleNotFoundError as exc:
    if not (exc.name or '').startswith('cryptography'):
        raise
    print(json.dumps({'ok': False, 'message': '订阅管理需要带 cryptography 的 Python；请修复 Science 的本地 Python 环境。'}, ensure_ascii=False))
    sys.exit(1)


class Manager:
    def __init__(self, home=None, runner=subprocess.run, env=None):
        self.home = Path(home or Path.home())
        self.runner = runner
        self.store = Store(self.home / '.csswitch-next/subscriptions')
        try:
            self.layout = resolve(self.home, env=env)
        except (OSError, ValueError):
            raise SubscriptionError('无法确定 Science 目录，请检查配置文件和 CSSWITCH_SCIENCE_DATA_DIR / CSSWITCH_SCIENCE_CONFIG。') from None
        binary = Path(self.layout['binary'])
        self.science = binary if binary.is_file() else None
        self.local_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def run(self, executable, args, timeout=20, env=None):
        if executable is None:
            raise SubscriptionError('未找到 Claude Science。')
        if executable == self.science and args and args[0] != '--version' and WINDOWS:
            args = [*args, '--data-dir', self.layout['data']]
            if Path(self.layout['config']).is_file():
                args += ['--config', self.layout['config']]
        try:
            result = self.runner([str(executable), *args], capture_output=True,
                                 text=True, encoding='utf-8', errors='replace', timeout=timeout, env=env,
                                 **subprocess_options())
        except subprocess.TimeoutExpired:
            raise SubscriptionError('操作超时，请刷新检查状态。') from None
        except OSError:
            raise SubscriptionError('无法运行本机管理组件。') from None
        if result.returncode:
            # Child output may include credentials or one-shot login URLs.
            raise SubscriptionError('本机组件未完成操作，请检查 Science 状态。')
        return result

    def science_state(self):
        if self.science is None:
            return {'running': False, 'installed': False}
        try:
            value = json.loads(self.run(self.science, ['status']).stdout)
            if not isinstance(value.get('running'), bool):
                raise ValueError()
            if value['running'] and (type(value.get('pid')) is not int or value['pid'] <= 0):
                raise ValueError()
            return {k: value[k] for k in ('running', 'pid', 'port', 'version', 'started_at') if k in value}
        except (ValueError, AttributeError):
            raise SubscriptionError('无法确认 Science 运行状态，未执行接入。') from None

    def base_url(self):
        settings = self.store.gateway()
        return f"http://127.0.0.1:{settings['port']}/{settings['secret']}"

    def gateway_request(self, path, post=False):
        req = urllib.request.Request(self.base_url() + path, data=b'' if post else None)
        with self.local_http.open(req, timeout=2) as response:
            return json.load(response)

    def health(self):
        try:
            value = self.gateway_request('/health')
            return value if value.get('service') == 'csswitch-subscriptions' else None
        except Exception:
            return None

    def ensure_gateway(self, allow_native=False):
        if self.store.snapshot().get('native_mode') and not allow_native:
            raise SubscriptionError('当前使用 Science 原生登录，模型请求直接访问官方服务，无需订阅网关。')
        health = self.health()
        if health:
            from subscription_gateway import USER_AGENT
            if health.get('version') == USER_AGENT.split('/')[-1]:
                return
            if health.get('active_requests'):
                raise SubscriptionError('订阅服务需要更新，当前回复完成后再试。Science 无需重启。')
            self.gateway_request('/shutdown', post=True)
            for _ in range(50):
                if not self.health(): break
                time.sleep(.1)
            else:
                raise SubscriptionError('旧订阅服务尚未退出，请稍后重试。')
        settings = self.store.gateway()
        with socket.socket() as probe:
            # Match the HTTP server: recent health connections can leave
            # TIME_WAIT sockets after a clean upgrade, without a live listener.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if WINDOWS else socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', settings['port']))
            except OSError:
                raise SubscriptionError('订阅网关端口被占用；未连接到其他程序。') from None
        child = subprocess.Popen([sys.executable, '-X', 'utf8', '-I', str(Path(__file__).with_name('subscription_gateway.py')),
                                  '--root', str(self.store.root)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True,
                                 **subprocess_options())
        for _ in range(40):
            if self.health():
                return
            if child.poll() is not None:
                break
            time.sleep(0.1)
        raise SubscriptionError('CSSwitch 订阅网关启动失败，请检查 Python 加密依赖或端口。')

    def owned(self, science):
        if WINDOWS:
            # The legacy inference launcher cannot attest a Windows process's
            # environment. Native switching does not rely on gateway ownership.
            return False
        path = self.store.root / 'science.json'
        if not science.get('running') or not path.exists():
            return False
        mark = json.loads(private_read(path))
        if any(science.get(k) != mark.get(k) for k in ('pid', 'port', 'started_at')):
            return False
        # PID reuse or a different launcher must never be reported as connected.
        try:
            process = self.run('/bin/ps', ['eww', '-p', str(science['pid']), '-o', 'command=']).stdout
            return 'ANTHROPIC_BASE_URL=' + self.base_url() in process
        except SubscriptionError:
            return False

    def status(self):
        science = self.science_state()
        health = self.health()
        public = self.store.public()
        last = (health or {}).get('last_inference')
        last = {k: last[k] for k in ('account_id', 'revision', 'started_at', 'completed_at') if k in last} if isinstance(last, dict) else None
        confirmed = bool(last and last.get('revision') == public['revision']
                         and any(a['current'] and a['id'] == last.get('account_id') for a in public['accounts']))
        from science_native_switch import NativeSwitcher
        native = NativeSwitcher(self).summary() if self.science is not None else {}
        return {**public, 'installed': self.science is not None, 'native': native,
                'hot_supported': not WINDOWS,
                'data_dir': self.layout['data'], 'history_found': self.layout['history_found'],
                'gateway': bool(health), 'active_requests': health.get('active_requests', 0) if health else 0,
                'science': science, 'connected': self.owned(science), 'last_inference': last,
                'live_validation': 'confirmed' if confirmed else 'pending'}

    def route(self, ident=None, prefer=False):
        state = self.store.snapshot()
        if state.get('native_mode') and not state.get('hot_mode'):
            raise SubscriptionError('当前使用原生登录切换；请选择「切换登录」，完成重启后在原会话 Resume。')
        before = self.science_state()
        if state.get('hot_mode') and (not self.owned(before) or not self.health()):
            raise SubscriptionError('免重启路由尚未连接，请点击「连接免重启模式」。')
        if prefer:
            self.ensure_gateway(allow_native=bool(state.get('hot_mode')))
        self.store.route(ident, prefer=prefer)
        after = self.science_state()
        same = all(before.get(k) == after.get(k) for k in ('running', 'pid', 'started_at'))
        message = ('已设置优先消耗，不可用时自动使用备用账号。' if prefer and ident else
                   '已关闭优先消耗，沿用当前账号；不可用时自动使用备用账号。' if prefer else
                   '已更换回复使用的账号。' if ident else '已开启自动使用备用账号。')
        return {'message': message + '正在进行的回复不受影响。'
                + ('' if same else ' Science 运行状态在外部发生变化，请检查任务。'),
                'same_process': same}

    def connect(self):
        if WINDOWS:
            raise SubscriptionError('Windows 请使用「切换登录」进入 Science 原生订阅模式。')
        before = self.science_state()
        if before.get('running'):
            if self.owned(before):
                self.ensure_gateway()
                return {'message': 'Science 已接入 CSSwitch；保留当前进程。', 'same_process': True}
            raise SubscriptionError('Science 正在运行且未接入 CSSwitch。请在任务完成后自行退出服务，再首次接入；当前进程保持运行。')
        if self.science is None:
            raise SubscriptionError('未找到 Claude Science。')
        if self.store.public()['ready_accounts'] < 1:
            raise SubscriptionError('请先添加有效订阅账号。')
        self.ensure_gateway()
        if self.science_state().get('running'):
            raise SubscriptionError('Science 已被其他程序启动，请刷新；未改动它。')
        env = {k: v for k, v in os.environ.items() if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL', 'BUN_OPTIONS')}
        env['ANTHROPIC_BASE_URL'] = self.base_url()
        self.run(self.science, ['serve', '--detached', '--no-browser'], timeout=150, env=env)
        after = self.science_state()
        if not after.get('running'):
            raise SubscriptionError('Science 启动尚未完成，请刷新状态。')
        private_write(self.store.root / 'science.json', json.dumps(after).encode())
        if not self.owned(after):
            raise SubscriptionError('Science 已运行，但未确认订阅网关接入；未停止它，请检查启动环境。')
        return {'message': 'Science 已接入 CSSwitch。保留 Science 自身登录，在原会话中继续使用。'}

    def manage(self, operation, params=None):
        params = params or {}
        if operation == 'usage':
            from subscription_usage import UsageService
            return UsageService(self).refresh(params.get('id'), params.get('force') is True)
        if operation == 'status':
            return self.status()
        if operation == 'sync_login':
            from science_native_switch import NativeSwitcher
            return NativeSwitcher(self).switch(params.get('id'), preserve_hot=True)
        if operation == 'switch':
            from science_native_switch import NativeSwitcher
            return NativeSwitcher(self).switch(params.get('id'))
        if operation in ('hot_enable', 'hot_disable'):
            from science_hot_switch import configure
            return configure(self, operation == 'hot_enable')
        if operation == 'recover':
            from science_native_switch import NativeSwitcher
            return NativeSwitcher(self).recover()
        if operation == 'delete':
            from science_native_switch import NativeSwitcher
            return NativeSwitcher(self).remove(params.get('id'))
        if operation == 'auto':
            return self.route()
        if operation in ('select', 'priority'):
            ident = params.get('id')
            if not isinstance(ident, str) or not ident:
                raise SubscriptionError('请选择账号。')
            if operation == 'priority' and params.get('enabled') is False:
                current = self.store.snapshot()
                if current.get('preferred', current['selected']) != ident:
                    return {'message': '此账号未设置优先消耗。'}
                return self.route(None, prefer=True)
            return self.route(ident, prefer=operation == 'priority')
        if operation == 'connect':
            return self.connect()
        if operation == 'start':
            self.ensure_gateway()
            return {'message': 'CSSwitch 订阅网关已就绪。'}
        if operation == 'login':
            self.ensure_gateway(allow_native=True)
            url = self.gateway_request('/login', post=True).get('url', '')
            if not url.startswith('https://claude.ai/oauth/authorize?'):
                raise SubscriptionError('登录地址无效，未打开浏览器。')
            self.open_url(url)
            return {'message': '已打开官方授权页。完成后刷新账号列表；添加第二个账号时请在官方页面切换登录。'}
        raise SubscriptionError('未知订阅管理操作。')

    def open_url(self, url):
        if WINDOWS:
            os.startfile(url)
        else:
            self.run('/usr/bin/open', [url])


def main():
    if len(sys.argv) > 1:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('operation', choices=['status', 'usage', 'auto', 'priority', 'select', 'sync_login', 'switch', 'recover', 'delete', 'connect', 'login', 'start', 'hot_enable', 'hot_disable'])
        parser.add_argument('id', nargs='?')
        args = parser.parse_args()
        request = {'operation': args.operation, 'params': {'id': args.id}}
    else:
        request = json.load(sys.stdin)
    try:
        manager = Manager()
        if request.get('operation') in ('usage', 'status'):
            # Reads must not contend with selections or native credential work.
            result = manager.manage(request['operation'], request.get('params'))
            print(json.dumps({'ok': True, 'value': result}, ensure_ascii=False))
            return 0
        path = manager.store.root / '.manager.lock'
        if linked(path):
            raise SubscriptionError('拒绝链接锁文件。')
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'r+b') as lock:
            protect(path)
            try:
                lock_file(lock.fileno(), blocking=False)
            except BlockingIOError:
                raise SubscriptionError('另一个订阅操作尚未完成，请稍后刷新。') from None
            result = manager.manage(request.get('operation'), request.get('params'))
        print(json.dumps({'ok': True, 'value': result}, ensure_ascii=False))
        return 0
    except SubscriptionError as exc:
        message = str(exc)
    except Exception:
        message = '订阅管理未完成；未返回敏感诊断信息。'
    print(json.dumps({'ok': False, 'message': message}, ensure_ascii=False))
    return 1


if __name__ == '__main__':
    sys.exit(main())
