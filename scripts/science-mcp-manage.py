#!/usr/bin/env python3
"""Private local-Science management bridge. Credentials arrive via stdin only.

No arbitrary request URLs/routes, redirects, external cookies, shell evaluation,
or persistent cookie jar. Science remains responsible for policy and MCP OAuth.
"""
import http.cookiejar
import base64
import ipaddress
import json
import os
import time
import re
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request
import importlib.util
from pathlib import Path

_local_spec = importlib.util.spec_from_file_location('science_local_skills', Path(__file__).with_name('science-local-skills.py'))
local_skills = importlib.util.module_from_spec(_local_spec)
_local_spec.loader.exec_module(local_skills)


_profiles_spec = importlib.util.spec_from_file_location('science_connectors', Path(__file__).with_name('science-connectors.py'))
profiles = importlib.util.module_from_spec(_profiles_spec)
_profiles_spec.loader.exec_module(profiles)


class BridgeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise BridgeError('Science 返回了意外重定向，未发送认证信息。')


def public_connector(value):
    # Deliberately never return command, args, env, headers, tokens or OAuth state.
    keys = ('id', 'name', 'displayName', 'source', 'description', 'authState',
            'transport', 'enabled', 'connectionStatus', 'admin_blocked', 'attachedAgents')
    public = {k: value[k] for k in keys if k in value}
    profile = value.get('name', '').removeprefix(profiles.PREFIX)
    if profile in profiles.NAMES and value.get('source') == 'local-stdio' and value.get('name', '').startswith(profiles.PREFIX) and value.get('description') == profiles.MARKER + profiles.NAMES.get(profile, ''):
        public['displayName'] = profiles.NAMES[profile]
    return public


class Bridge:
    def __init__(self, base, nonce, data_dir=None, auth_dir=None, home=None):
        u = urllib.parse.urlsplit(base)
        if u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port or u.username or u.password or u.path or u.query or u.fragment:
            raise BridgeError('管理接口必须是 CSSwitch 所拥有的本地 Science 服务。')
        if not isinstance(nonce, str) or not nonce:
            raise BridgeError('缺少 Science 本地会话凭据。')
        self.base = base
        # These paths come from the owned native session, never frontend params.
        self.data_dir, self.auth_dir = data_dir, auth_dir
        self.home = home
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), urllib.request.HTTPCookieProcessor(self.jar))
        self.request('/api/auth/nonce', {'nonce': nonce, 'dest': '/'})
        self.request('/api/csrf')  # 204 + Set-Cookie is the normal response.
        if not any(c.name == 'operon_csrf' for c in self.jar):
            raise BridgeError('无法获取 Science CSRF 凭据，请刷新后重试。')

    def request(self, path, data=None, method=None, content_type=None):
        if not path.startswith('/api/') or '://' in path or '#' in path:
            raise BridgeError('不支持的管理接口。')
        headers = {'Origin': self.base}
        csrf = next((c.value for c in self.jar if c.name == 'operon_csrf'), None)
        if csrf:
            headers['x-operon-csrf'] = csrf
        if data is not None:
            headers['Content-Type'] = content_type or 'application/json'
        body = data if isinstance(data, bytes) else json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(self.base + path, data=body, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=15) as response:
                body = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as e:
            # Remote servers may echo keys in errors: never forward the raw body.
            hint = {401: '本地认证已失效，请刷新重试。', 403: 'Science 策略或授权禁止此操作。',
                    404: '连接器或接口不存在；请刷新并检查 Science 版本。',
                    400: '参数被 Science 拒绝，请检查名称、传输方式和地址。',
                    409: '名称已存在，请选择其他名称。',
                    503: 'Science 目录或服务暂不可用，请恢复连接后刷新。'}.get(e.code, '请在 Science 中检查连接器状态。')
            status = e.code
            e.close()
            raise BridgeError(f'Science HTTP {status}：{hint}') from None
        if len(body) > 2 * 1024 * 1024:
            raise BridgeError('Science 管理响应过大。')
        if not body or path == '/api/auth/nonce':
            return {}
        try:
            return json.loads(body)
        except ValueError:
            raise BridgeError(f'Science 返回了无法解析的管理数据（{path}）。') from None

    def connectors(self):
        data = self.request('/api/mcp-servers/connectors')
        if isinstance(data, dict):
            data = data.get('connectors', [])
        if not isinstance(data, list):
            raise BridgeError('Science 连接器列表格式不兼容。')
        return data

    def run(self, operation, params):
        if operation.startswith('skills_'):
            return self.skills(operation, params)
        if operation == 'install':
            return self.install_science(params)
        if operation == 'export_science':
            # Export fresh specs with no stored keys, cookies or auth material.
            try:
                return {'mcpServers': {key: {k: v for k, v in profiles.spec(params['home'], key, params['launcher'], params['proxy_port'], params.get('node')).items() if k in ('command', 'args', 'env')} for key in profiles.NAMES}}
            except (ValueError, KeyError) as e:
                raise BridgeError(str(e)) from None
        if operation == 'list':
            connectors = [public_connector(c) for c in self.connectors()]
            health = None
            try:
                raw = self.request('/api/mcp-servers/directory-health').get('directoryHealth', {})
                health = {k: raw[k] for k in ('ok', 'reason', 'status') if k in raw}
            except BridgeError:
                pass
            policy = self.request('/api/org-policy')
            return {'connectors': connectors, 'directoryHealth': health,
                    'customAllowed': policy.get('caps', {}).get('operon_custom_connectors', {}).get('allowed', False),
                    'scienceProfiles': profiles.catalog(connectors, self.home)}
        if operation in ('add_local', 'add_remote'):
            name = params.get('name')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', name):
                raise BridgeError('MCP 名称请使用 1–64 位字母、数字、下划线或短横线。')
            if any(c.get('name') == name for c in self.connectors()):
                raise BridgeError('MCP 名称已存在；请使用列表中的绑定操作或更改名称。')
            data = {k: params[k] for k in ('name', 'description') if k in params}
            if operation != 'add_remote':
                command = params.get('command')
                arguments = params.get('args', [])
                env = params.get('env', {})
                if not isinstance(command, str) or not command.startswith('/') or not isinstance(arguments, list) or not all(isinstance(a, str) for a in arguments):
                    raise BridgeError('本地 MCP 需要可执行文件的绝对路径，参数需为 JSON 字符串数组。')
                if not isinstance(env, dict) or not all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k) and isinstance(v, str) for k, v in env.items()):
                    raise BridgeError('环境变量需为 JSON 字符串对象。')
                data.update(command=command, args=arguments, env=env)
                added = self.request('/api/mcp-servers/local', data)
            else:
                u = urllib.parse.urlsplit(params.get('url', ''))
                if u.scheme != 'https' or not u.hostname or u.username or u.password or u.fragment or u.hostname == 'localhost':
                    raise BridgeError('远程 MCP 需要公共 HTTPS 地址；本机服务请选择 stdio。')
                try:
                    if not ipaddress.ip_address(u.hostname).is_global:
                        raise BridgeError('远程 MCP 需要公共 HTTPS 地址。')
                except ValueError:
                    pass  # Science also validates DNS and organizational policy.
                if params.get('transport') not in ('sse', 'streamable_http'):
                    raise BridgeError('远程传输方式必须是 streamable_http 或 sse。')
                data.update({k: params[k] for k in ('url', 'transport', 'oauth_server_url', 'client_id', 'scopes', 'headers_helper') if params.get(k)})
                added = self.request('/api/mcp-servers', data)
            sid = added.get('id')
            if not isinstance(sid, str) or not sid:
                raise BridgeError('连接器已添加，但 Science 未返回 ID；请刷新查看。')
            if params.get('attach', True):
                try:
                    self.request('/api/agents/OPERON/connectors', {'server_id': sid})
                except BridgeError:
                    return {'connector': public_connector(added), 'attached': False, 'message': '已添加，但绑定失败。请刷新后点击绑定 Science。'}
            return {'connector': public_connector(added), 'attached': bool(params.get('attach', True)), 'message': 'MCP 已添加；工具权限由 Science 管理。'}
        connector = next((c for c in self.connectors() if c.get('id') == params.get('id')), None)
        if not connector:
            raise BridgeError('连接器已不存在，请刷新。')
        sid = urllib.parse.quote(connector['id'], safe='')
        source = connector.get('source')
        if operation == 'tools':
            raw = self.request(f'/api/mcp-servers/{sid}/tool-permissions?source={urllib.parse.quote(source, safe="")}')
            return {'status': raw.get('status'), 'skipApprovalsActive': raw.get('skipApprovalsActive', False),
                    'tools': [{k: t[k] for k in ('toolName', 'description', 'readOnlyHint', 'state') if k in t} for t in raw.get('tools', [])]}
        if operation == 'grant':
            if params.get('decision') not in ('allow', 'deny', 'ask', None) or not isinstance(params.get('toolName'), str) or not params['toolName'] or params['toolName'] == '*':
                raise BridgeError('不支持的工具权限。')
            self.request(f'/api/mcp-servers/{sid}/tool-grants', {k: params[k] for k in ('toolName', 'decision')})
        elif operation in ('attach', 'detach'):
            if source not in ('custom', 'local-stdio', 'antmcp'):
                raise BridgeError('此连接器请在 Science 中管理绑定。')
            if operation == 'attach':
                self.request('/api/agents/OPERON/connectors', {'server_id': connector['id']})
            else:
                self.request(f'/api/agents/OPERON/connectors/{sid}', method='DELETE')
        elif operation == 'enabled':
            if source != 'bundled' or not isinstance(params.get('enabled'), bool):
                raise BridgeError('仅内置连接器支持开关；自定义 MCP 使用绑定/解绑。')
            self.request(f'/api/mcp-servers/connectors/{sid}/enabled', {'enabled': params['enabled']}, 'PUT')
        elif operation == 'remove':
            if source not in ('local-stdio', 'custom'):
                raise BridgeError('此连接器不能在 CSSwitch 删除。')
            self.request(f'/api/mcp-servers/{"local/" if source == "local-stdio" else ""}{sid}', method='DELETE')
        elif operation == 'authorize':
            raw = self.request(f'/api/mcp-servers/connectors/{sid}/authorize', {})
            if raw.get('kind') == 'redirect':
                u = urllib.parse.urlsplit(raw.get('authorizeUrl', ''))
                if u.scheme != 'https' or not u.hostname or u.username or u.password:
                    raise BridgeError('Science 返回的授权地址不受支持。')
                return {'authorizeUrl': raw['authorizeUrl'], 'message': '请在浏览器完成服务商授权后刷新。'}
            if raw.get('kind') != 'authorized':
                raise BridgeError('服务商授权失败，请在 Science 中检查连接器配置。')
            return {'message': '连接器已授权。'}
        else:
            raise BridgeError('不支持的 MCP 管理操作。')
        return {'ok': True}

    def saved_local_env(self, home, name):
        source = Path(home) / '.claude-science/mcp/local-mcp.json'
        if source.is_symlink() or not source.is_file():
            raise BridgeError('无法读取已有本机连接器配置，未覆盖其环境变量。')
        try:
            rows = json.loads(source.read_text()).get('servers', [])
            if isinstance(rows, dict):
                entry = rows.get(name, {})
            else:
                entry = next((row for row in rows if row.get('name') == name), {})
            if entry.get('description') != profiles.MARKER + profiles.NAMES[name.removeprefix(profiles.PREFIX)]:
                raise ValueError()
            env = entry.get('env', {})
            if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
                raise ValueError()
            return dict(env)
        except (ValueError, KeyError, TypeError):
            raise BridgeError('已有连接器配置不兼容，未覆盖其环境变量。') from None

    def backup_legacy(self, home):
        source = Path(home) / '.claude-science/mcp/local-mcp.json'
        if source.is_symlink() or not source.is_file():
            raise BridgeError('旧连接器配置无法安全备份。')
        destination = Path(home) / '.csswitch-next/mcp-migration-backups'
        if destination.is_symlink():
            raise BridgeError('旧连接器备份目录无效。')
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(destination, 0o700)
        path = destination / f'local-mcp-{time.time_ns()}.json'
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, 'wb') as output:
            output.write(source.read_bytes())
            output.flush()
            os.fsync(output.fileno())

    def install_science(self, params):
        policy = self.request('/api/org-policy')
        if not policy.get('caps', {}).get('operon_custom_connectors', {}).get('allowed'):
            raise BridgeError('Science 组织策略禁止自定义连接器。')
        try:
            # All 24 profiles must have actual local resources, before any mutation.
            specs = {key: profiles.spec(params['home'], key, params['launcher'], params['proxy_port'], params.get('node'), params.get('env')) for key in profiles.NAMES}
        except (ValueError, KeyError) as e:
            raise BridgeError(str(e)) from None
        report, complete = [], True
        for key in profiles.HOSTED:
            desired = specs[key]
            existing = next((c for c in self.connectors() if c.get('name') == desired['name']), None)
            try:
                if existing:
                    if existing.get('source') != 'local-stdio' or existing.get('description') != desired['description']:
                        raise BridgeError('同名连接器不是 CSSwitch 管理的本机版本，请先更名。')
                    # POST is an upsert in Science 0.1.62. Preserve stored user
                    # env while repairing executable paths/proxy configuration.
                    saved = self.saved_local_env(params['home'], desired['name'])
                    saved.update(params.get('env') or {})
                    desired['env'] = {**saved, **profiles.proxy_env(params['proxy_port'])}
                    self.request('/api/mcp-servers/local', {k: v for k, v in desired.items() if k != 'attach'})
                    if 'OPERON' not in existing.get('attachedAgents', []):
                        self.run('attach', {'id': existing['id']})
                    sid = existing['id']
                else:
                    added = self.run('add_local', desired)
                    sid = added['connector']['id']
                    if not added['attached']:
                        raise BridgeError(added['message'])
                tools = self.run('tools', {'id': sid})
                for _ in range(12):
                    if tools.get('status') != 'pending':
                        break
                    time.sleep(0.25)
                    tools = self.run('tools', {'id': sid})
                if tools.get('status') != 'loaded' or not tools.get('tools'):
                    raise BridgeError('本机服务尚未就绪，未停用对应官方服务；请稍后重新接入。')
                official = next((c for c in self.connectors() if c.get('source') == 'bundled' and c.get('name') == key), None)
                if official and official.get('enabled', True):
                    self.run('enabled', {'id': official['id'], 'enabled': False})
                report.append({'profile': key, 'displayName': profiles.NAMES[key], 'ok': True, 'tools': len(tools['tools'])})
            except BridgeError as e:
                complete = False
                report.append({'profile': key, 'displayName': profiles.NAMES[key], 'ok': False, 'message': str(e)})
        # Existing native-local profiles keep their original IDs, credentials,
        # grants and Ketcher host.app identity. Do not silently enable user-disabled tools.
        state = profiles.catalog(self.connectors(), params['home'], params.get('node'))
        if complete:
            old = next((c for c in self.connectors() if c.get('source') == 'local-stdio' and c.get('name') == 'csswitch-openscience' and c.get('description') == '科研数据库 MCP 服务'), None)
            if old:
                try:
                    self.backup_legacy(params['home'])
                    self.run('remove', {'id': old['id']})
                except (BridgeError, OSError, ValueError):
                    complete = False
                    report.append({'displayName': '旧聚合服务', 'ok': False, 'message': '旧配置备份或移除失败，已保留；请重新接入重试。'})
        ready = sum(p['ready'] for p in state)
        return {'complete': complete, 'report': report, 'scienceProfiles': state,
                'message': f'本机连接器接入完成：{ready}/24 已连接。' if complete else '部分连接器未就绪，详细结果见接入窗口；可重新接入补齐。'}

    def skills(self, operation, params):
        if operation == 'skills_list':
            return self.skill_catalog()
        if operation == 'skills_update':
            catalog = self.skill_catalog()
            if not catalog.get('customAllowed') or not catalog.get('localImportAvailable'):
                raise BridgeError(catalog.get('localImportReason') or '本地技能更新暂不可用。')
            name = params.get('name')
            entry = next((s for s in catalog['skills'] if s['name'] == name), {})
            if entry.get('source') != 'csswitch-local' or entry.get('admin_blocked'):
                raise BridgeError('此技能由 Science 或组织管理，无法覆盖更新。')
            try:
                actual, _, files = local_skills.unpack(params.get('filename'), params.get('bundle'))
                if actual != name:
                    raise BridgeError('新技能 name 与已安装名称不一致。')
                store = self.local_store()
                with store.replacing(name, files, params.get('expectedFingerprint'), params.get('sourceKey')):
                    self.verify_local_skill(name, files)
                return {'name': name, 'message': 'Skill 已更新，原有启停状态和代理绑定保留。'}
            except local_skills.LocalSkillError as error:
                raise BridgeError(str(error)) from None
        if operation == 'skills_import':
            catalog = self.skill_catalog()
            if not catalog.get('customAllowed'):
                raise BridgeError('Science 组织策略禁止添加自定义 Skills。')
            if catalog.get('degraded'):
                if not catalog.get('localImportAvailable'):
                    raise BridgeError(catalog.get('localImportReason') or '本地技能安装暂不可用。')
                store = self.local_store()
                try:
                    name, _, files = local_skills.unpack(params.get('filename'), params.get('bundle'))
                    reserved = {s['name'] for s in catalog['skills']}
                    store.install(name, files, reserved, params.get('sourceKey'))
                    try:
                        self.verify_local_skill(name, files)
                        attached = self.request('/api/agents/OPERON/skills', {'skill_name': name})
                        if name not in attached.get('skillNames', []):
                            raise BridgeError('Science 未能绑定本地技能，安装已撤销。')
                    except Exception:
                        try:
                            self.request(f'/api/agents/OPERON/skills/{name}', method='DELETE')
                        except BridgeError:
                            pass
                        store.remove(name)
                        raise
                    return {'name': name, 'message': 'Skill 已安装到 Science 本地并绑定 OPERON，无需官方目录登录。'}
                except local_skills.LocalSkillError as error:
                    raise BridgeError(str(error)) from None
            filename = params.get('filename', '')
            if not re.fullmatch(r'[A-Za-z0-9_. -]{1,160}\.(zip|skill|md)', filename, re.I):
                raise BridgeError('请选择 ZIP、.skill 或 Markdown Skill 文件。')
            try:
                content = base64.b64decode(params.get('bundle', ''), validate=True)
            except (ValueError, TypeError):
                raise BridgeError('Skill 文件编码无效。') from None
            if not content or len(content) > local_skills.MAX_BYTES:
                raise BridgeError('Skill 文件必须在 256 MB 以内。')
            # Random boundary cannot appear in the encoded transport framing.
            import secrets
            boundary = 'csswitch-' + secrets.token_hex(24)
            body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                    + content + f'\r\n--{boundary}--\r\n'.encode())
            result = self.request('/api/skills/import', body, 'POST', 'multipart/form-data; boundary=' + boundary)
            return {'name': result.get('name'), 'message': 'Skill 已导入 Science，默认审批由 Science 管理。'}
        name = params.get('name')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', name):
            raise BridgeError('Skill 名称无效。')
        sid = urllib.parse.quote(name, safe='')
        if operation == 'skills_content':
            return self.request(f'/api/skills/catalog/{sid}/content?path=SKILL.md')
        if operation == 'skills_enabled':
            if not isinstance(params.get('enabled'), bool):
                raise BridgeError('Skill 开关必须是布尔值。')
            self.request(f'/api/skills/catalog/{sid}/enabled', {'enabled': params['enabled']}, 'PUT')
            return {'message': 'Skill 状态已更新。'}
        if operation == 'skills_remove':
            store = self.local_store(optional=True)
            try:
                managed = store.info(name) if store else None
                if managed:
                    catalog = self.skill_catalog()
                    if not catalog.get('customAllowed'):
                        raise BridgeError('Science 组织策略禁止管理自定义 Skills。')
                    # Refuse published or reclassified content even if its marker survived.
                    entry = next((s for s in catalog['skills'] if s['name'] == name), {})
                    if entry.get('source') != 'csswitch-local':
                        raise BridgeError('此技能已由 Science 目录管理，未删除本地文件。')
                    for agent in entry.get('attachedAgents', []):
                        self.request(f'/api/agents/{urllib.parse.quote(agent, safe="")}/skills/{sid}', method='DELETE')
                    store.remove(name)
                    return {'message': 'Science 本地 Skill 已卸载。'}
            except local_skills.LocalSkillError as error:
                raise BridgeError(str(error)) from None
            # Science verifies source, policy, and published ownership before deleting.
            self.request(f'/api/skills/{sid}/full', method='DELETE')
            return {'message': 'Skill 已卸载。'}
        raise BridgeError('不支持的 Skills 管理操作。')

    def verify_local_skill(self, name, files):
        if name not in self.request('/api/skills/drafts').get('drafts', []):
            raise BridgeError('Science 未识别本地技能，修改已撤销。')
        for path, data in files.items():
            if path != 'SKILL.md': continue
            content = self.request(f'/api/skills/catalog/{name}/content?path=SKILL.md')
            if content.get('content') != data.decode('utf-8'):
                raise BridgeError('Science 未能读取新技能内容，修改已撤销。')

    def local_store(self, optional=False):
        if not self.data_dir or not self.auth_dir:
            if optional:
                return None
            raise BridgeError('缺少本地技能管理资源，请安装新版 CSSwitch。')
        try:
            return local_skills.LocalSkills(self.data_dir, self.auth_dir)
        except local_skills.LocalSkillError as error:
            if optional:
                return None
            raise BridgeError(str(error)) from None

    def skill_catalog(self):
        catalog = self.request('/api/skills/catalog')
        policy = self.request('/api/org-policy')
        catalog['customAllowed'] = policy.get('caps', {}).get('operon_custom_skills', {}).get('allowed') is True
        catalog['localImportAvailable'] = False
        catalog['localImportReason'] = ''
        try:
            drafts = self.request('/api/skills/drafts').get('drafts', [])
        except BridgeError:
            drafts = []
            catalog['localImportReason'] = '当前 Science 版本无法验证本地草稿，请升级 Science。'
        try:
            store = self.local_store()
        except BridgeError as error:
            store = None
            catalog['localImportReason'] = str(error)
        if store and not catalog['localImportReason']:
            me = self.request('/api/me')
            try:
                disabled = store.disabled(me['user_id'])
                raw = self.request('/api/agents')
                agents = raw if isinstance(raw, list) else raw.get('agents', [])
                existing = {s['name'] for s in catalog.get('skills', [])}
                for name in drafts:
                    if name in existing or not isinstance(name, str) or not local_skills.NAME.fullmatch(name):
                        continue
                    info = store.info(name) or {'name': name, 'source': 'draft', 'description': 'Science 本地技能草稿'}
                    info.update(enabled=name not in disabled, attachedAgents=[a['name'] for a in agents if name in a.get('skillNames', [])])
                    catalog.setdefault('skills', []).append(info)
                catalog['localImportAvailable'] = catalog['customAllowed']
            except local_skills.LocalSkillError as error:
                catalog['localImportReason'] = str(error)
        if not catalog['customAllowed']:
            catalog['localImportReason'] = 'Science 组织策略禁止添加自定义 Skills。'
        return catalog


def main():
    def timeout(_signum, _frame):
        raise BridgeError('Science 管理操作超时，请刷新后重试。')
    local_skills._platform.deadline(85)
    try:
        data = json.loads(sys.stdin.read(local_skills.MAX_REQUEST))
        value = Bridge(data['base'], data['nonce'], data.get('data_dir'), data.get('auth_dir'), data.get('home')).run(data['operation'], data.get('params', {}))
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    except BridgeError as e:
        print(json.dumps({'ok': False, 'message': str(e)}, ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok': False, 'message': '无法连接 Science 管理接口，请确认服务已启动并重试。'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
