#!/usr/bin/env python3
"""Manual, credential-free network observations. Never imports account services."""
import concurrent.futures
from contextlib import contextmanager
from email.utils import parsedate_to_datetime
import hashlib
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from csswitch_platform import deadline, lock_file
from subscription_platform import linked, open_read, protect, subprocess_options

INTERVAL = 300
MIN_INTERVAL = 60
INTELLIGENCE_TTL = 86400
TRACE_URLS = {
    'api': 'https://api.anthropic.com/cdn-cgi/trace',
    'web': 'https://claude.ai/cdn-cgi/trace',
    'general': 'https://www.cloudflare.com/cdn-cgi/trace',
}
CHINA_URL = 'https://2026.ip138.com/'
STATUS_URL = 'https://status.claude.com/api/v2/status.json'
REGION_SOURCE = 'https://support.claude.com/en/articles/8461763-where-can-i-access-claude'
REGION_VERIFIED = '2026-11-10'
# Manually mapped ISO codes from the official list above, retrieved 2026-11-10.
# Includes its "Mariana Islands" (MP) and "Herzegovina" (already included as BA).
SUPPORTED_REGIONS = set('''AL DZ AD AO AG AR AM AU AT AZ BS BH BD BB BE BZ BJ BT BO BA
BW BR BN BG BF BI KH CM CA CV TD CL CO KM CG CR HR CZ DK DJ DM DO TL EC EG SV GQ
EE SZ FJ FI FR GA GM GE DE GH GR GD GT GN GW GY HT HN HU IS IN ID IQ IE IL IT CI
JM JP JO KZ KE KI KW KG LA LV LB LS LR LI LT LU MG MW MY MV MT MP MH MR MU MX FM
MD MC MN ME MA MZ NA NR NP NL NZ NE NG MK NO OM PK PW PS PA PG PY PE PH PL PT QA
CY RO RW KN LC VC WS SM ST SA SN RS SC SL SG SK SI SB ZA KR ES LK SR SE CH TW TJ
TZ TH TG TO TT TN TR TM TV UG UA AE GB US UY UZ VU VA VN ZM ZW'''.split())


def public_ip(value):
    try:
        ip = ipaddress.ip_address(value)
        return str(ip) if ip.is_global else None
    except (ValueError, TypeError):
        return None


def short_text(value):
    return ''.join(c for c in value if c.isprintable())[:120] if isinstance(value, str) else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(url):
    """Fixed HTTPS destinations only; no cookies, account headers, or redirects."""
    parsed = urllib.parse.urlsplit(url)
    intelligence_url = (
        parsed.scheme == 'https' and parsed.netloc == 'api.ipapi.is' and parsed.path == '/'
        and public_ip(urllib.parse.parse_qs(parsed.query).get('q', [''])[0])
        and set(urllib.parse.parse_qs(parsed.query)) == {'q'}
    ) or (parsed.scheme == 'https' and parsed.netloc == 'ip.net.coffee' and not parsed.query and (
        any(parsed.path.startswith(prefix) and public_ip(parsed.path.removeprefix(prefix)) for prefix in ('/api/iprisk/', '/api/geoip/'))
        or re.fullmatch(r'/api/dns/result/[a-f0-9]{24}', parsed.path)))
    if url not in (*TRACE_URLS.values(), CHINA_URL, STATUS_URL) and not intelligence_url:
        raise ValueError('Unsupported public endpoint')
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'CSSwitch-Environment/1.0', 'Accept': 'application/json, text/plain', 'Cache-Control': 'no-cache'})
        # Same urllib/system proxy discovery as CSSwitch's subscription gateway.
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=7) as response:
            body = response.read(131073)
            if len(body) > 131072:
                return {'error': 'invalid_response'}
            return {'status': response.status, 'body': body.decode('utf-8'),
                    'date': response.headers.get('Date'), 'latency_ms': round((time.monotonic() - started) * 1000)}
    except urllib.error.HTTPError as error:
        retry = error.headers.get('Retry-After', '') if error.headers else ''
        try:
            retry_seconds = float(retry) if retry.isdigit() else parsedate_to_datetime(retry).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            retry_seconds = 900
        result = {'status': error.code, 'error': 'http'}
        if error.code == 429 or retry:
            result['retry_seconds'] = max(1, min(86400, retry_seconds))
        return result
    except (ssl.SSLError, urllib.error.URLError) as error:
        return {'error': 'tls' if isinstance(getattr(error, 'reason', error), ssl.SSLError) else 'network'}
    except (TimeoutError, OSError):
        return {'error': 'network'}
    except (ValueError, UnicodeError):
        return {'error': 'invalid_response'}


def trace(result):
    base = {k: result[k] for k in ('status', 'error', 'latency_ms') if k in result}
    if result.get('status') != 200 or result.get('error'):
        return base
    fields = dict(line.split('=', 1) for line in result.get('body', '').splitlines() if '=' in line)
    ip = public_ip(fields.get('ip'))
    if not ip:
        return {**base, 'error': 'invalid_response'}
    country = fields.get('loc', '')
    base.update(ip=ip, country=country if re.fullmatch('[A-Z]{2}', country) else None,
                tls=short_text(fields.get('tls')), ok=True)
    try:
        base['clock_offset'] = round(parsedate_to_datetime(result['date']).timestamp() - time.time())
    except (KeyError, TypeError, ValueError, OverflowError):
        pass
    return base


def intelligence(result, ip, source='ipapi.is'):
    base = {'ip': ip, 'source': source, 'status': 'unknown'}
    if result.get('status') != 200 or result.get('error'):
        return {**base, 'error': 'rate_limited' if result.get('status') == 429 else result.get('error', 'network'),
                'retry_seconds': result.get('retry_seconds', 900)}
    try:
        value = json.loads(result.get('body', ''))
        if not isinstance(value, dict) or value.get('error'):
            return {**base, 'error': 'invalid_response'}
        returned_ip = public_ip(value.get('ip'))
        scope = None
        if source == 'Net.Coffee' and returned_ip and isinstance(value.get('cidr'), str):
            try:
                network = ipaddress.ip_network(value['cidr'], strict=True)
                if network.prefixlen >= (24 if network.version == 4 else 48) and ipaddress.ip_address(ip) in network and ipaddress.ip_address(returned_ip) in network:
                    scope = str(network)
            except ValueError: pass
        if returned_ip != ip and not scope:
            return {**base, 'error': 'invalid_response'}
        flags = {key: value.get(key) if type(value.get(key)) is bool else None
                 for key in ('is_tor', 'is_abuser', 'is_datacenter', 'is_proxy', 'is_vpn', 'is_crawler')}
        asn = value.get('asn') if isinstance(value.get('asn'), dict) else {}
        location = value.get('location') if isinstance(value.get('location'), dict) else {}
        if source == 'Net.Coffee':
            # A source-provided reputation score, never an Anthropic score or ban probability.
            asn = {'asn': value.get('asn'), 'org': value.get('asOrganization')}
            location = {'country': value.get('country'), 'city': value.get('city')}
        score = value.get('trust_score') if source == 'Net.Coffee' else None
        score = score if type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 100 else None
        return {**base, 'status': 'ok' if all(v is not None for v in flags.values()) else 'partial', **flags,
                'organization': short_text(asn.get('org')), 'asn': asn.get('asn') if type(asn.get('asn')) is int else None,
                'country': short_text(location.get('country')), 'city': short_text(location.get('city')),
                'timezone': short_text(value.get('timezone') if source == 'Net.Coffee' else location.get('timezone')),
                'is_residential': value.get('isResidential') if type(value.get('isResidential')) is bool else None,
                'company_type': short_text(value.get('company_type')), 'trust_score': score,
                'scope': 'network' if scope else 'ip', 'cidr': scope}
    except (ValueError, TypeError):
        return {**base, 'error': 'invalid_response'}


def local_network():
    # Raw settings can contain proxy credentials/PAC URLs; only hash them, never store/output them.
    proxies = urllib.request.getproxies()
    raw = json.dumps(proxies, sort_keys=True)
    pac = False
    if sys.platform == 'darwin':
        for args in (['/usr/sbin/scutil', '--proxy'], ['/sbin/route', '-n', 'get', 'default'], ['/usr/sbin/scutil', '--nwi']):
            try:
                result = subprocess.run(args, capture_output=True, text=True, timeout=2, **subprocess_options())
                raw += '\n'.join(line for line in result.stdout.splitlines() if re.search(r'^\s*(gateway|interface|destination):', line)) if 'route' in args[0] else result.stdout
                if 'scutil' in args[0]:
                    pac = bool(re.search(r'ProxyAuto(?:ConfigEnable|DiscoveryEnable)\s*:\s*1', result.stdout))
            except (OSError, subprocess.TimeoutExpired):
                pass
    return {'fingerprint': hashlib.sha256(raw.encode()).hexdigest(), 'proxy': bool(proxies.get('https') or proxies.get('http')),
            'pac': pac, 'socks': bool(proxies.get('all') or proxies.get('socks'))}


def assess(observations, intel, previous, now, local):
    checks = []
    def add(key, level, title, detail):
        checks.append({'id': key, 'level': level, 'title': title, 'detail': detail})
    names = {'api': 'Anthropic API', 'web': 'Claude 网页', 'general': 'Cloudflare 对照'}
    for key, row in observations.items():
        if row.get('ok'):
            add(key, 'ok', names[key] + '连通', 'HTTPS 证书验证通过；公开诊断端点可达，不代表账号登录或模型请求一定成功。')
        else:
            reason = '证书校验失败' if row.get('error') == 'tls' else ('HTTP ' + str(row['status']) if row.get('status') else '连接超时或失败')
            add(key, 'unknown', names[key] + '未确认', reason + '。未携带账号凭据，不能据此判断账号被封。')
    api, web = observations['api'], observations['web']
    target = api if api.get('ok') else web
    if target.get('country'):
        age = now - time.mktime(time.strptime(REGION_VERIFIED, '%Y-%m-%d'))
        if age > 180 * 86400:
            add('region', 'unknown', '地区名单需要更新', '内置官方名单超过 180 天，请核对官方支持地区。')
        elif target['country'] not in SUPPORTED_REGIONS:
            add('region', 'warning', '出口地区未列入官方支持名单', target['country'] + ' 不在 ' + REGION_VERIFIED + ' 核对的名单中，请核对官方服务地区要求。')
        else:
            add('region', 'ok', '出口地区在支持名单内', target['country'] + ' · 官方名单核对于 ' + REGION_VERIFIED + '；出口地区不能证明使用者所在地或账号资格。')
    else:
        add('region', 'unknown', '出口地区未知', '未能取得 Claude 或 API 的有效出口信息。')
    ips = [r['ip'] for r in observations.values() if r.get('ok')]
    if len(set(ips)) > 1:
        add('routing', 'notice', '不同域名出口不一致', '可能存在分流、代理或 IPv4 / IPv6 差异；这本身不表示账号异常。')
    elif len(ips) == 3:
        add('routing', 'ok', '三个探测出口一致', '仅代表本次诊断请求；Science 的按进程代理或既有连接可能不同。')
    else:
        add('routing', 'unknown', '出口对照不完整', '缺少有效出口，暂不能比较。')
    if local.get('pac') or local.get('socks'):
        add('proxy', 'notice', '存在 PAC 或 SOCKS 配置', '当前探测器未执行 PAC、未使用 SOCKS；不能据此确认 Science 的实际线路。')
    else:
        add('proxy', 'notice' if local.get('proxy') else 'ok', '检测到系统 / 环境代理' if local.get('proxy') else '未检测到显式 HTTP 代理', '探测沿用 CSSwitch 网关的系统 / 环境代理规则。TUN、分流及 Science 进程独立代理可能无法识别。')
    labels = [('is_tor', 'Tor 出口'), ('is_abuser', '滥用记录'), ('is_datacenter', '机房 IP'), ('is_proxy', '代理 IP'), ('is_vpn', 'VPN IP'), ('is_crawler', '机器人 IP')]
    for flag, label in labels:
        value = intel.get(flag) if intel.get('status') in ('ok', 'partial') else None
        level = 'unknown' if value is None else ('warning' if flag in ('is_tor', 'is_abuser') else 'notice') if value else 'ok'
        add(flag, level, label + ('：未知' if value is None else '：有标记' if value else '：未标记'),
            intel.get('source', '第三方') + (' 的同网段缓存记录' if intel.get('scope') == 'network' else ' 的第三方记录') + '，可能过时或误判，不是 Anthropic 风控结果。' if value is not None else '第三方情报不可用或未返回此字段，不能视为无风险。')
    offsets = [r['clock_offset'] for r in observations.values() if 'clock_offset' in r]
    if len(offsets) >= 2 and max(offsets) - min(offsets) < 60:
        offset = sum(offsets) / len(offsets)
        add('clock', 'warning' if abs(offset) > 300 else 'ok', '系统时间偏差较大' if abs(offset) > 300 else '系统时间基本一致', '与 HTTPS 响应时间比较，偏差约 ' + str(round(abs(offset))) + ' 秒。')
    else:
        add('clock', 'unknown', '系统时间未确认', '需要至少两个一致的 HTTPS 服务时间才能判断。')
    history = [h for h in previous.get('history', []) if 0 <= now - h.get('at', 0) < 86400][-48:]
    last = dict(previous.get('last_exits', {}))
    for key in ('api', 'web'):
        row = observations[key]
        if not row.get('ok'):
            continue
        old = last.get(key, {})
        if old.get('ip') and old['ip'] != row['ip'] and 0 <= now - old.get('at', 0) < 86400:
            history.append({'at': now, 'endpoint': key, 'from': old['ip'], 'to': row['ip'],
                            'from_country': old.get('country'), 'to_country': row.get('country')})
        last[key] = {'ip': row['ip'], 'country': row.get('country'), 'at': now}
    changes = [h for h in history if h['endpoint'] == 'api']
    country_changes = [h for h in history if h.get('from_country') and h.get('to_country') and h['from_country'] != h['to_country']]
    if country_changes:
        add('changes', 'warning', '24 小时内观察到出口跨地区变化', '请检查网络是否稳定、是否有非预期的代理变更；不会自动切换账号或线路。')
    elif len(changes) >= 3:
        add('changes', 'warning', '24 小时内 API 出口多次变化', '观察到 ' + str(len(changes)) + ' 次变化，请检查网络稳定性；这不是封号预测。')
    else:
        add('changes', 'notice', '出口变化记录', '24 小时内观察到 ' + str(len(changes)) + ' 次 API 出口变化。应用关闭期间无法监测。')
    level = 'warning' if any(c['level'] == 'warning' for c in checks) else 'unknown' if any(c['level'] == 'unknown' for c in checks) else 'observed'
    return {'checks': checks, 'level': level, 'history': history[-48:], 'last_exits': last}


class Monitor:
    def __init__(self, root=None, requester=request, network=local_network, clock=time.time):
        self.root = Path(root or Path.home() / '.csswitch-next/environment')
        self.requester, self.network, self.clock = requester, network, clock

    def prepare(self):
        if any(linked(p) for p in (self.root, *self.root.parents)):
            raise ValueError('环境检测目录不能为链接。')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        protect(self.root)

    def read(self):
        path = self.root / 'state.json'
        try:
            if any(linked(p) for p in (path, *path.parents)):
                raise ValueError('拒绝链接检测记录。')
            fd = open_read(path)
            with os.fdopen(fd, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError('无效检测记录。')
                value = json.loads(stream.read(262145))
                if not isinstance(value, dict) or value.get('schema') != 1:
                    raise ValueError('无效检测记录。')
                return value
        except FileNotFoundError:
            return {'schema': 1, 'enabled': True}

    def write(self, value):
        fd, name = tempfile.mkstemp(prefix='.environment-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as stream:
                protect(name)
                json.dump(value, stream, ensure_ascii=False, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            if linked(self.root / 'state.json'):
                raise ValueError('拒绝链接检测记录。')
            os.replace(name, self.root / 'state.json')
        finally:
            if os.path.exists(name): os.unlink(name)

    @contextmanager
    def locked(self):
        self.prepare()
        path = self.root / '.lock'
        if linked(path): raise ValueError('拒绝链接锁文件。')
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        with os.fdopen(fd, 'r+b') as stream:
            protect(path)
            lock_file(stream.fileno())
            yield

    def public(self, value):
        result = {k: value[k] for k in ('enabled', 'checked_at', 'observations', 'intelligence', 'checks', 'level', 'history', 'service_status', 'dns', 'udp') if k in value}
        result.update(interval=INTERVAL, min_interval=MIN_INTERVAL, region_source=REGION_SOURCE, region_verified=REGION_VERIFIED)
        age = self.clock() - value.get('checked_at', 0)
        result['stale'] = not value.get('checked_at') or age > INTERVAL + 60 or age < 0
        result['next_check_at'] = max(value.get('checked_at', 0) + INTERVAL, value.get('retry_at', 0)) if value.get('enabled') else None
        result['history'] = [h for h in result.get('history', []) if 0 <= self.clock() - h.get('at', 0) < 86400]
        cooldowns = value.get('endpoint_cooldowns', {})
        if all(cooldowns.get(key, 0) > self.clock() for key in (*TRACE_URLS, 'china', 'service')):
            result['refresh_deferred_until'] = min(cooldowns.values())
        return result

    def geo(self, ip):
        row = self.requester('https://ip.net.coffee/api/geoip/' + ip)
        try:
            value = json.loads(row.get('body', '')) if row.get('status') == 200 else {}
            if not isinstance(value, dict) or value.get('ip') and public_ip(value['ip']) != ip: return {}
            country = value.get('country_code', '')
            return {'country_code': country.upper() if isinstance(country, str) and re.fullmatch('[a-zA-Z]{2}', country) else None,
                    **{key: short_text(value.get(key)) for key in ('country', 'city', 'isp')}}
        except (ValueError, TypeError): return {}

    def deep_check(self, value, operation, ips):
        now = self.clock()
        if 0 <= now - value.get(operation, {}).get('checked_at', 0) < 60:
            return value
        if operation == 'dns':
            token = secrets.token_hex(12)
            def resolve(index):
                try: socket.getaddrinfo(f'{token}-{index}.d.ip.net.coffee', 443)
                except OSError: pass
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(resolve, (1, 2)))
            ips = []
            for _ in range(2):
                time.sleep(0.75)
                response = self.requester('https://ip.net.coffee/api/dns/result/' + token)
                try:
                    data = json.loads(response.get('body', '')) if response.get('status') == 200 else {}
                    raw = data.get('dns_servers', []) if isinstance(data, dict) else []
                    ips = [public_ip(ip) for ip in raw if isinstance(ip, str) and public_ip(ip)][:3]
                except (ValueError, TypeError): pass
                if ips: break
        else:
            ips = [public_ip(ip) for ip in (ips or []) if isinstance(ip, str) and public_ip(ip)][:3]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            rows = [dict(ip=ip, **geo) for ip, geo in zip(ips, pool.map(self.geo, ips))]
        reference = value.get('observations', {}).get('web', {})
        if not reference.get('ok'): reference = value.get('observations', {}).get('api', {})
        mismatch = any(row.get('country_code') and reference.get('country') and row['country_code'] != reference['country'] for row in rows) if operation == 'dns' else any(reference.get('ip') and row['ip'] != reference['ip'] for row in rows)
        comparable = bool(rows and reference.get('ip')) and (operation == 'udp' or bool(reference.get('country')) and all(row.get('country_code') for row in rows))
        value[operation] = {'checked_at': now, 'rows': rows, 'reference_ip': reference.get('ip'),
                            'status': 'different' if mismatch else 'observed' if comparable else 'unknown'}
        return value

    def manage(self, operation, enabled=None, ips=None):
        if operation == 'status':
            return self.public(self.read())
        if operation not in ('tick', 'refresh', 'configure', 'clear', 'dns', 'udp'):
            raise ValueError('未知环境检测操作。')
        with self.locked():
            value = self.read()
            if operation in ('dns', 'udp'):
                value = self.deep_check(value, operation, ips)
                self.write(value)
                return self.public(value)
            if operation == 'configure':
                if type(enabled) is not bool: raise ValueError('监测开关参数无效。')
                value['enabled'] = enabled
                self.write(value)
                return self.public(value)
            if operation == 'clear':
                value = {'schema': 1, 'enabled': value.get('enabled', True),
                         'retry_at': max(value.get('retry_at', 0), value.get('checked_at', 0) + MIN_INTERVAL),
                         'intelligence_retry_at': value.get('intelligence_retry_at', 0),
                         'primary_retry_at': value.get('primary_retry_at', 0),
                         'endpoint_cooldowns': value.get('endpoint_cooldowns', {})}
                self.write(value)
                return self.public(value)
            if operation == 'tick' and not value.get('enabled', True):
                return self.public(value)
            now = self.clock()
            local = self.network()
            elapsed = now - value.get('checked_at', 0)
            changed = local['fingerprint'] != value.get('network_fingerprint')
            if operation == 'tick' and (0 <= elapsed < MIN_INTERVAL or value.get('retry_at', 0) > now):
                return self.public(value)
            if operation == 'tick' and not changed and 0 <= elapsed < INTERVAL:
                return self.public(value)
            urls = {**TRACE_URLS, 'china': CHINA_URL, 'service': STATUS_URL}
            # A manual probe always opens fresh connections. Only an explicit
            # server cooldown blocks that endpoint, never the other routes.
            cooldowns = {key: until for key, until in value.get('endpoint_cooldowns', {}).items()
                         if key in urls and until > now}
            ready = {key: url for key, url in urls.items() if key not in cooldowns}
            if not ready:
                return self.public(value)
            results = {key: {'error': 'rate_limited', 'retry_seconds': until - now}
                       for key, until in cooldowns.items()}
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                results.update(zip(ready, pool.map(self.requester, ready.values())))
            for key in ready:
                retry = results[key].get('retry_seconds', 900 if results[key].get('status') == 429 else 0)
                if retry > 0:
                    cooldowns[key] = now + retry
            value['endpoint_cooldowns'] = cooldowns
            observations = {key: trace(results[key]) for key in TRACE_URLS}
            target_key = 'api' if observations['api'].get('ok') else 'web'
            ip = observations[target_key].get('ip')
            cache = value.get('intelligence_cache', {})
            intel = {'status': 'unknown', 'source': 'Net.Coffee'}
            if ip:
                cached = cache.get(ip, {})
                if cached.get('retry_at', 0) > now and now >= cached.get('checked_at', 0):
                    intel = cached
                elif value.get('intelligence_retry_at', 0) > now:
                    intel = {'ip': ip, 'status': 'unknown', 'source': 'Net.Coffee', 'error': 'backoff'}
                else:
                    if value.get('primary_retry_at', 0) <= now:
                        intel = intelligence(self.requester('https://ip.net.coffee/api/iprisk/' + ip), ip, 'Net.Coffee')
                        if intel['status'] == 'unknown':
                            value['primary_retry_at'] = now + max(900, intel.get('retry_seconds', 900))
                    if intel['status'] == 'unknown':
                        intel = intelligence(self.requester('https://api.ipapi.is/?' + urllib.parse.urlencode({'q': ip})), ip)
                    intel['checked_at'] = now
                    intel['retry_at'] = now + (INTELLIGENCE_TTL if intel['status'] == 'ok' else max(900, intel.get('retry_seconds', 900)))
                    if intel['status'] not in ('ok', 'partial'):
                        value['intelligence_retry_at'] = intel['retry_at']
                    cache[ip] = intel
                intel = {**intel, 'endpoint': target_key}
            assessed = assess(observations, intel, value, now, local)
            # The domestic-site probe is a separate route, never assumed to be a Chinese IP.
            candidates = re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])', results['china'].get('body', ''))
            domestic_ip = next((public_ip(candidate) for candidate in candidates if public_ip(candidate)), None) if results['china'].get('status') == 200 else None
            observations['china'] = {'ok': bool(domestic_ip), 'ip': domestic_ip, 'source': 'ip138.com'}
            if domestic_ip:
                old_china = value.get('observations', {}).get('china', {})
                reused_geo = old_china.get('ip') == domestic_ip and 0 <= now - old_china.get('geo_at', 0) < (INTELLIGENCE_TTL if old_china.get('geo') else 900)
                geo = old_china.get('geo', {}) if reused_geo else self.geo(domestic_ip)
                observations['china'].update(geo=geo, country=geo.get('country_code'), geo_at=old_china.get('geo_at', now) if reused_geo else now)
            try:
                service = json.loads(results['service'].get('body', '')).get('status', {}).get('indicator')
                service = service if service in ('none', 'minor', 'major', 'critical', 'maintenance') else None
            except (ValueError, AttributeError): service = None
            failures = value.get('failures', 0) + 1 if not any(r.get('ok') for r in observations.values()) else 0
            retry_delay = max([r.get('retry_seconds', 0) for r in results.values()] + [min(1800, INTERVAL * 2 ** min(failures - 1, 3)) if failures else 0])
            value.update(assessed, checked_at=now, observations=observations, intelligence=intel,
                         service_status=service, network_fingerprint=local['fingerprint'], failures=failures, retry_at=now + retry_delay)
            value['intelligence_cache'] = dict(sorted(cache.items(), key=lambda item: item[1].get('checked_at', 0))[-8:])
            self.write(value)
            return self.public(value)


if __name__ == '__main__':
    timer = deadline(45)
    try:
        params = json.loads(sys.stdin.buffer.read(4096))
        result = Monitor().manage(params.get('operation'), params.get('enabled'), params.get('ips'))
        print(json.dumps({'ok': True, 'value': result}, ensure_ascii=False, allow_nan=False))
    except Exception:
        # Never return exception text, raw responses, proxy URLs or environment variables.
        print(json.dumps({'ok': False, 'message': '环境检测未完成，请稍后重试或检查本地检测记录。'}, ensure_ascii=False))
    finally:
        timer.cancel()
