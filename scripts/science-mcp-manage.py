#!/usr/bin/env python3
"""Private local-Science management bridge. Credentials arrive via stdin only.

No arbitrary request URLs/routes, redirects, external cookies, shell evaluation,
or persistent cookie jar. Science remains responsible for policy and MCP OAuth.
"""
import http.cookiejar
import base64
import ipaddress
import json
import re
import signal
import sys
import urllib.error
import urllib.parse
import urllib.request


class BridgeError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise BridgeError('Science 返回了意外重定向，未发送认证信息。')


def public_connector(value):
    # Deliberately never return command, args, env, headers, tokens or OAuth state.
    keys = ('id', 'name', 'displayName', 'source', 'description', 'authState',
            'transport', 'enabled', 'connectionStatus', 'admin_blocked', 'attachedAgents')
    return {k: value[k] for k in keys if k in value}


class Bridge:
    def __init__(self, base, nonce):
        u = urllib.parse.urlsplit(base)
        if u.scheme != 'http' or u.hostname != '127.0.0.1' or not u.port or u.username or u.password or u.path or u.query or u.fragment:
            raise BridgeError('管理接口必须是 CSSwitch 所拥有的本地 Science 服务。')
        if not isinstance(nonce, str) or not nonce:
            raise BridgeError('缺少 Science 本地会话凭据。')
        self.base = base
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
                    'customAllowed': policy.get('caps', {}).get('operon_custom_connectors', {}).get('allowed', False)}
        if operation in ('add_local', 'add_remote', 'install'):
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

    def skills(self, operation, params):
        if operation == 'skills_list':
            return self.request('/api/skills/catalog')
        if operation == 'skills_import':
            filename = params.get('filename', '')
            if not re.fullmatch(r'[A-Za-z0-9_. -]{1,160}\.(zip|skill|md)', filename, re.I):
                raise BridgeError('请选择 ZIP、.skill 或 Markdown Skill 文件。')
            try:
                content = base64.b64decode(params.get('bundle', ''), validate=True)
            except (ValueError, TypeError):
                raise BridgeError('Skill 文件编码无效。') from None
            if not content or len(content) > 4 * 1024 * 1024:
                raise BridgeError('Skill 文件必须在 4 MB 以内。')
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
            # Science verifies source, policy, and published ownership before deleting.
            self.request(f'/api/skills/{sid}/full', method='DELETE')
            return {'message': 'Skill 已卸载。'}
        raise BridgeError('不支持的 Skills 管理操作。')


def main():
    signal.alarm(85)
    try:
        data = json.loads(sys.stdin.read(6 * 1024 * 1024))
        value = Bridge(data['base'], data['nonce']).run(data['operation'], data.get('params', {}))
        print(json.dumps({'ok': True, 'value': value}, ensure_ascii=False))
    except BridgeError as e:
        print(json.dumps({'ok': False, 'message': str(e)}, ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok': False, 'message': '无法连接 Science 管理接口，请确认服务已启动并重试。'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
