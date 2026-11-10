// Native monitoring only reads public network data. No subscription command here.
const environmentState = { data: null, busy: false, error: '', generation: 0 };

function environmentStale(data = environmentState.data) {
  const age = Date.now() / 1000 - (data?.checked_at || 0);
  return !data?.checked_at || data.stale || age > (data.interval || 300) + 60 || age < 0;
}
function environmentSummary() {
  const d = environmentState.data;
  if (environmentState.error) return { label: '检测不可用', tone: 'unknown' };
  if (!d) return { label: '待检测', tone: 'unknown' };
  if (d.refresh_deferred_until > Date.now() / 1000) return { label: '检测服务冷却中', tone: 'notice' };
  if (environmentStale(d)) return { label: d.checked_at ? '结果待更新' : '等待首次检测', tone: 'unknown' };
  return { label: d.level === 'warning' ? '发现需关注项' : d.level === 'unknown' ? '检测信息不完整' : '已完成检查', tone: d.level || 'unknown' };
}
function environmentTime(value) {
  return value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未检测';
}
function environmentIP(value) {
  if (!value) return '未取得出口';
  if (localStorage.getItem('csswitch-environment-show-ip') !== 'false') return value;
  return value.includes(':') ? value.split(':').slice(0, 2).join(':') + ':••••' : value.split('.').slice(0, 2).join('.') + '.•••.•••';
}
function updateEnvironmentIndicators() {
  const summary = environmentSummary();
  const badge = $('#environmentBadge');
  if (badge) {
    badge.hidden = false; badge.dataset.tone = summary.tone;
    badge.textContent = '环境 · ' + summary.label;
    badge.title = '查看网络环境检测'; badge.onclick = () => showView('environment');
  }
  const alert = $('#environmentAlert');
  const warnings = environmentState.data?.checks?.filter(c => c.level === 'warning') || [];
  if (alert) {
    alert.hidden = state.view === 'environment' || environmentStale() || !warnings.length || Boolean(environmentState.error);
    alert.querySelector('span').textContent = warnings[0]?.title || '';
    alert.querySelector('button').onclick = () => showView('environment');
  }
  const stamp = $('#environmentUpdated');
  if (stamp) stamp.textContent = '上次联网检查：' + environmentTime(environmentState.data?.checked_at) + (environmentStale() ? ' · 当前状态待确认' : '');
}
function acceptEnvironment(data) {
  if (!data || typeof data.enabled !== 'boolean') throw new Error('环境检测数据无效。');
  environmentState.data = data; environmentState.error = ''; environmentState.generation++;
  updateEnvironmentIndicators();
  if (state.view === 'environment' && $('#environmentPanel')) renderEnvironment();
}
async function loadEnvironment(refresh = false) {
  if (!native()) return;
  if (refresh) return environmentAction('refresh');
  const generation = environmentState.generation;
  try {
    const data = await call('environment_manage', { operation: 'status' });
    if (generation === environmentState.generation) acceptEnvironment(data);
  } catch (_) {
    environmentState.error = '无法读取环境检测结果，请重试。';
    updateEnvironmentIndicators();
  }
}
async function environmentAction(operation, enabled, ips) {
  if (!native() || environmentState.busy) return;
  environmentState.busy = true; environmentState.error = '';
  if (state.view === 'environment') renderEnvironment();
  try {
    acceptEnvironment(await call('environment_manage', { operation, ...(typeof enabled === 'boolean' ? { enabled } : {}), ...(ips ? { ips } : {}) }));
  } catch (_) {
    environmentState.error = '环境检测未完成，请稍后重试。上次结果仅供参考。';
  } finally {
    environmentState.busy = false;
    updateEnvironmentIndicators();
    if (state.view === 'environment') renderEnvironment();
  }
}
let environmentDevice = null;
let environmentDevicePromise = null;
function environmentFlag(country) {
  return /^[A-Z]{2}$/.test(country || '') ? `<img class="env-flag" src="./assets/flags/${country.toLowerCase()}.svg" alt="${esc(country)}" width="24" height="18">` : '';
}
function environmentTag(label, tone = 'unknown') { return `<span class="env-tag ${tone}">${esc(label)}</span>`; }
function environmentRow(label, value, title = '') { return `<div class="env-row" ${title ? `title="${esc(title)}"` : ''}><span>${esc(label)}</span><div>${value}</div></div>`; }
function environmentBool(value, yes = '有标记', no = '未标记', risk = true) {
  return value === true ? environmentTag(yes, risk ? 'warn' : 'neutral') : value === false ? environmentTag(no, 'good') : environmentTag('未知');
}
function environmentDeepMarkup(kind, data) {
  const stale = !data?.checked_at || Date.now() / 1000 - data.checked_at > 600 || data.reference_ip !== (environmentState.data?.observations?.web?.ip || environmentState.data?.observations?.api?.ip);
  const status = !data ? '未检测' : stale ? '结果待更新' : data.status === 'different' ? '出口存在差异' : data.status === 'observed' ? '本次未见差异' : '未取得有效结果';
  const tone = stale || !data || data.status === 'unknown' ? 'unknown' : data.status === 'different' ? 'warn' : 'good';
  const rows = data?.rows || [];
  return environmentRow('状态', environmentTag(status, tone)) +
    environmentRow(kind === 'dns' ? 'DNS 出口' : 'UDP 出口', rows.length ? rows.map(r => `<span>${environmentFlag(r.country_code)} ${esc(environmentIP(r.ip))}</span>`).join('<br>') : '—') +
    environmentRow(kind === 'dns' ? '服务商' : '归属地', esc(rows.map(r => kind === 'dns' ? r.isp : r.country).filter(Boolean).join(' / ') || '未知')) +
    `<p class="env-card-note">${kind === 'dns' ? '通过系统 DNS 查询验证。递归解析器地区不同仅提示路径差异，不等于泄露或账号风险。' : '仅检测本窗口的 WebRTC / STUN 路径；未返回候选不等于没有泄露，也不代表 Science。'}${data?.checked_at ? '<br>检测于 ' + esc(environmentTime(data.checked_at)) : ''}</p>`;
}
async function inspectEnvironmentDevice() {
  const digest = async value => {
    if (!crypto.subtle) return '当前环境不支持';
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return [...new Uint8Array(bytes)].slice(0, 4).map(b => b.toString(16).padStart(2, '0')).join('').toUpperCase();
  };
  const device = { timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || '未知', language: navigator.language || '未知',
    ua: navigator.userAgent, cookie: navigator.cookieEnabled, renderer: '未取得', canvas: '未取得', webgl: '未取得' };
  try {
    const canvas = document.createElement('canvas'); canvas.width = 220; canvas.height = 45;
    const ctx = canvas.getContext('2d');
    if (ctx) { ctx.font = '16px Arial'; ctx.fillStyle = '#cc785c'; ctx.fillText('CSSwitch · 环境检测', 8, 27); device.canvas = await digest(canvas.toDataURL()); }
    const gl = document.createElement('canvas').getContext('webgl');
    if (gl) {
      const ext = gl.getExtension('WEBGL_debug_renderer_info');
      device.renderer = gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER);
      device.webgl = await digest(JSON.stringify([device.renderer, gl.getParameter(gl.VENDOR), gl.getParameter(gl.VERSION), gl.getParameter(gl.MAX_TEXTURE_SIZE), gl.getSupportedExtensions()]));
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
  } catch (_) { /* Restricted browser environments remain explicitly unknown. */ }
  environmentDevice = device;
  if (state.view === 'environment' && $('#environmentPanel')) renderEnvironment();
}
async function inspectEnvironmentUDP() {
  if (environmentState.busy || !native()) return;
  environmentState.busy = true; renderEnvironment();
  const ips = new Set(); let pc = null; let timer;
  try {
    if (typeof RTCPeerConnection === 'function') {
      pc = new RTCPeerConnection({ iceServers: [{ urls: 'stun:stun.cloudflare.com:3478' }] });
      await new Promise(resolve => {
        let finished = false;
        const done = () => { if (finished) return; finished = true; clearTimeout(timer); resolve(); };
        timer = setTimeout(done, 7000);
        pc.onicecandidate = event => {
          if (!event.candidate) { done(); return; }
          const parts = event.candidate.candidate.split(/\s+/);
          if (parts[7] === 'srflx' && parts[2]?.toLowerCase() === 'udp') ips.add(event.candidate.address || parts[4]);
        };
        pc.createDataChannel('environment');
        pc.createOffer().then(offer => pc.setLocalDescription(offer)).catch(done);
      });
    }
    acceptEnvironment(await call('environment_manage', { operation: 'udp', ips: [...ips].slice(0, 3) }));
  } catch (_) { environmentState.error = 'WebRTC 检测未完成；没有结果不能视为无泄露。'; }
  finally {
    clearTimeout(timer); pc?.close(); environmentState.busy = false; updateEnvironmentIndicators();
    if (state.view === 'environment') renderEnvironment();
  }
}
function renderEnvironment() {
  const focused = $('#environmentPanel')?.contains(document.activeElement) ? document.activeElement.id : null;
  const d = environmentState.data;
  const summary = environmentSummary();
  const o = d?.observations || {}, intel = d?.intelligence || {}, device = environmentDevice;
  const disabled = environmentState.busy || !native() ? 'disabled' : '';
  const hiddenIP = localStorage.getItem('csswitch-environment-show-ip') === 'false';
  const score = Number.isFinite(intel.trust_score) && intel.trust_score >= 0 && intel.trust_score <= 100 ? intel.trust_score : null;
  const region = d?.checks?.find(c => c.id === 'region');
  const regionTag = region?.level === 'ok' ? environmentTag('名单内', 'good') : region?.level === 'warning' ? environmentTag('未列入', 'warn') : environmentTag('未知');
  const ipType = intel.is_residential === true ? environmentTag('家庭住宅 IP', 'good') : intel.is_datacenter === true ? environmentTag('机房 IP', 'neutral') : environmentTag('属性未确认');
  const flag = environmentFlag(o.api?.country || o.web?.country);
  const primary = o.api?.ok ? o.api : o.web;
  const exits = [
    ['china', '国内站点出口 IPv4', 'globe-2', o.china, o.china?.geo ? [o.china.geo.country, o.china.geo.city, o.china.geo.isp].filter(Boolean).join(' · ') : '通过 ip138.com 探测'],
    ['general', 'Cloudflare 出口 IP', 'cloud', o.general, o.general?.country || '用于比较分流路径'],
    ['api', 'Claude / API 出口 IP', 'asterisk', primary, [intel.country, intel.city, intel.organization].filter(Boolean).join(' · ') || primary?.country || '公开 HTTPS 诊断出口'],
  ];
  const serviceNames = { none: '全部服务正常', minor: '轻微故障', major: '重大故障', critical: '严重故障', maintenance: '维护中' };
  const connection = key => o[key]?.ok ? `${environmentTag(o[key].latency_ms > 600 ? '较慢' : '可达', o[key].latency_ms > 600 ? 'warn' : 'good')} <small>${esc(o[key].latency_ms ?? '—')}ms</small>` : environmentTag('未确认');
  let timezone = device?.timezone || '正在读取…';
  if (intel.timezone) timezone += ' / 出口：' + intel.timezone;
  const localDevice = `<section class="env-card env-wide environment-device"><h3>用户设备信息 <small>仅本机显示</small></h3>
    ${environmentRow('时区', esc(timezone))}${environmentRow('语言', esc(device?.language || '—'))}
    ${environmentRow('操作系统 / 浏览器', esc(native() ? 'CSSwitch · 系统 WebView' : device?.ua || '—'), device?.ua)}
    ${environmentRow('Cookie', device ? environmentTag(device.cookie ? '浏览器报告已启用' : '浏览器报告已禁用', 'neutral') : '—')}
    ${environmentRow('WebGL 渲染器', esc(device?.renderer || '—'))}${environmentRow('Canvas 指纹', `<code>${esc(device?.canvas || '—')}</code>`)}
    ${environmentRow('WebGL 指纹', `<code>${esc(device?.webgl || '—')}</code>`)}<p class="env-card-note">当前 CSSwitch 窗口的本地摘要，不上传、不修改，也不代表 Science 的浏览器指纹。语言与时区仅展示，不据此判断账号风险。</p></section>`;
  $('#content').innerHTML = `<section id="environmentPanel" class="environment-panel">
    <div class="env-page-heading"><div><h2>Claude AI IP 风险检测</h2><p>检测出口 IP、网络属性、安全记录、DNS、UDP、设备信息与出口历史。</p></div><label class="environment-ip-toggle">隐藏 IP <input id="environmentShowIP" type="checkbox" ${hiddenIP ? 'checked' : ''}><span class="environment-switch" aria-hidden="true"></span></label></div>
    <div class="environment-summary"><h3>${esc(summary.label)}</h3><span id="environmentUpdated"></span><div class="env-toolbar"><span class="env-manual-label">仅手动检测</span><button class="btn" id="environmentRefresh" ${disabled}>${icon('refresh-cw')}${environmentState.busy ? '检测中…' : '立即检测'}</button></div></div>
    ${environmentState.error ? `<div class="environment-error" role="alert">${esc(environmentState.error)}</div>` : ''}
    ${d?.checked_at && environmentStale() ? '<p class="env-stale-note">以下为上次检测结果，当前环境状态待确认。</p>' : ''}
    ${d?.refresh_deferred_until > Date.now() / 1000 ? `<p class="env-stale-note" role="status">检测服务暂时限频，本次未重新联网；可在 ${esc(environmentTime(d.refresh_deferred_until))} 后重试。</p>` : ''}
    <div class="environment-exits">${exits.map(([key, name, symbol, row, geo]) => `<article class="env-card environment-exit"><h3>${icon(symbol)}${name}</h3><strong class="${row?.ip?.includes(':') ? 'env-ipv6' : ''}">${environmentFlag(row?.country)}<span class="environment-ip-value">${row?.ip ? esc(environmentIP(row.ip)) : row?.error === 'rate_limited' ? '限频中' : d?.checked_at ? '获取失败' : '等待检测'}</span></strong><p title="${esc(geo)}">${esc(geo)}</p></article>`).join('')}</div>
    <div class="env-grid">
      <section class="env-card env-score"><h3>IP 信誉参考评分 <small>TRUST SCORE</small></h3><div class="env-score-value">${score === null ? '<strong class="unknown">—</strong>' : `<strong style="color:${score >= 75 ? '#27b968' : score >= 40 ? '#cf962f' : '#ce5646'}">${score.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}</strong>`}${environmentTag(score === null ? '暂无评分' : intel.scope === 'network' ? '网段参考' : '第三方参考', score === null ? 'unknown' : 'neutral')}</div><p class="env-score-source">${score === null ? '来源未返回有效评分' : esc(intel.source || '第三方') + ' 返回的 IP 信誉分'}</p><div class="env-score-track">${score === null ? '' : `<span style="left:${score}%"></span>`}</div><div class="env-score-scale"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>${environmentRow('Claude 支持地区', regionTag, region?.detail)}<p class="env-card-note">不是 Claude 官方评分，不能保证避免封号。未取得的数据不计作正常。</p></section>
      <section class="env-card env-attributes"><h3>Claude / API 出口 IP 属性</h3>${environmentRow('地区', `${flag} ${esc(intel.country || primary?.country || '未知')}`)}${environmentRow('城市', esc(intel.city || '未知'))}${environmentRow('IP 属性', ipType)}${environmentRow('ASN', intel.asn ? 'AS' + esc(intel.asn) : '未知')}${environmentRow('运营商', esc(intel.organization || '未知'))}<p class="env-card-note">来源：${esc(intel.source || '待查询')} · ${esc(environmentTime(intel.checked_at))}</p></section>
      <section class="env-card env-security"><h3>Claude / API 出口 IP 安全检测</h3>${environmentRow('VPN', environmentBool(intel.is_vpn, '有标记', '未标记', false))}${environmentRow('代理 (Proxy)', environmentBool(intel.is_proxy, '有标记', '未标记', false))}${environmentRow('Tor', environmentBool(intel.is_tor))}${environmentRow('机器人 (Crawler)', environmentBool(intel.is_crawler, '是', '否', false))}${environmentRow('滥用记录', environmentBool(intel.is_abuser, '有记录', '未标记'))}<p class="env-card-note">${intel.scope === 'network' ? '当前是同网段缓存，非此 IP 的单独记录。' : '第三方数据库记录，可能存在延迟和误判。'}</p></section>
      <section class="env-card env-connectivity"><h3>Claude 可用性检测</h3>${environmentRow('claude.ai', connection('web'))}${environmentRow('api.anthropic.com', connection('api'))}${environmentRow('Claude 服务状态', environmentTag(serviceNames[d?.service_status] || '未知', d?.service_status === 'none' ? 'good' : d?.service_status ? 'warn' : 'unknown'))}<p class="env-card-note">公开端点连通性与官方状态页结果；不验证账号登录，不调用推理或额度接口。</p></section>
      <section class="env-card env-dns"><h3>DNS 出口检测<button id="environmentDNS" class="btn" ${disabled}>检测 DNS</button></h3>${environmentDeepMarkup('dns', d?.dns)}</section>
      <section class="env-card env-udp"><h3>WebRTC UDP 出口检测<button id="environmentUDP" class="btn" ${disabled}>深度检测</button></h3>${environmentDeepMarkup('udp', d?.udp)}</section>
      ${localDevice}
      <section class="env-card env-wide environment-history"><div class="env-card-heading"><div><h3>Claude / API 出口 IP 历史记录</h3><p>最近 24 小时 · 仅本机保存 · 最多 48 条变化记录</p></div><button class="btn" id="environmentClear" ${disabled}>清除</button></div><div class="env-history-grid">${primary?.ip ? `<div class="env-history-item current"><time>${esc(environmentTime(d?.checked_at))} <span>当前</span></time><strong>${flag} ${esc(environmentIP(primary.ip))}</strong></div>` : ''}${d?.history?.length ? d.history.slice(-8).reverse().map(h => `<div class="env-history-item"><time>${esc(environmentTime(h.at))} · ${h.endpoint === 'api' ? 'API' : '网页'}</time><strong>${environmentFlag(h.to_country)} ${esc(environmentIP(h.from))} → ${esc(environmentIP(h.to))}</strong></div>`).join('') : '<p class="subtle">暂无观察记录；不代表过去没有变化。</p>'}</div></section>
    </div>
    <details class="environment-privacy env-card"><summary>连接环境详情与提醒</summary>${(d?.checks || []).filter(c => !c.id.startsWith('is_')).map(c => environmentRow(c.title, environmentTag(({ ok:'通过', warning:'需关注', unknown:'未知', notice:'信息' })[c.level] || '未知', ({ ok:'good', warning:'warn', notice:'neutral' })[c.level] || 'unknown'), c.detail) + `<p class="env-card-note">${esc(c.detail)}</p>`).join('')}</details>
    <details class="environment-privacy env-card"><summary>监测频率、检测范围与数据来源</summary><p>仅手动检测。打开页面展示上次结果，不在后台定时联网。切换代理后点击“立即检测”即可重新探测当前出口，不受旧的 60 秒缓存限制；服务明确限频时显示冷却状态。DNS 与 WebRTC 按需手动检测。</p><p>来源：Claude / Anthropic / Cloudflare 公开诊断、ip138.com 国内站点、Claude 官方状态页；IP 情报优先使用 Net.Coffee，失败时查询 ipapi.is。有效情报缓存 24 小时。第三方接口接收查询 IP 并可见访问来源，失败或限频显示未知。</p><p>DNS 检测向 Net.Coffee 的随机诊断域名发起系统解析；WebRTC 使用 Cloudflare STUN，观察到的公网候选可向 Net.Coffee 查询归属地。不上传账号邮箱、令牌、对话或本地设备指纹，不自动切换账号、改代理或中断任务。</p><p>探测沿用 CSSwitch 的系统 / 环境代理规则，不执行 PAC 或 SOCKS；TUN、按进程分流和 Science 已有连接可能不同。出口信息不能代表实际居住地或账号资格。官方地区名单核对于 ${esc(d?.region_verified || '2026-11-10')}，超过 180 天标记待更新。</p><p>记录位于 ~/.csswitch-next/environment。手动清除会保留必要的限频退避时间，不影响账号、会话和额度刷新设置。</p><div class="actions"><button class="btn" id="environmentRegionSource">官方支持地区 ${icon('external-link')}</button><button class="btn" id="environmentReference">参考检测网站 ${icon('external-link')}</button></div></details>
  </section>`;
  $('#environmentRefresh').onclick = () => environmentAction('refresh');
  $('#environmentShowIP').onchange = e => { localStorage.setItem('csswitch-environment-show-ip', String(!e.target.checked)); renderEnvironment(); };
  $('#environmentDNS').onclick = () => environmentAction('dns');
  $('#environmentUDP').onclick = inspectEnvironmentUDP;
  $('#environmentClear').onclick = async () => {
    if (await confirmAction('清除环境检测记录？', '将清除本机检测结果、出口变化历史和 IP 情报缓存。需要点击“立即检测”重新获取结果。')) environmentAction('clear');
  };
  $('#environmentRegionSource').onclick = () => call('environment_open_source', { source: 'regions' }).catch(() => notice('无法打开浏览器。', true));
  $('#environmentReference').onclick = () => call('environment_open_source', { source: 'reference' }).catch(() => notice('无法打开浏览器。', true));
  icons(); updateEnvironmentIndicators();
  if (!environmentDevice && !environmentDevicePromise) environmentDevicePromise = inspectEnvironmentDevice();
  if (focused) document.getElementById(focused)?.focus({ preventScroll: true });
}
async function initEnvironment() {
  if (!native()) return;
  if (window.__TAURI__.event?.listen) {
    try {
      await window.__TAURI__.event.listen('environment-updated', event => acceptEnvironment(event.payload));
      await window.__TAURI__.event.listen('environment-error', () => {
        environmentState.error = '环境检测暂不可用，请手动重试。';
        updateEnvironmentIndicators();
        if (state.view === 'environment') renderEnvironment();
      });
    } catch (_) { environmentState.error = '检测状态通知不可用，请手动刷新查看。'; }
  }
  await loadEnvironment();
}
// Local age display only: this timer never initiates network or quota requests.
setInterval(() => { if (!document.hidden) updateEnvironmentIndicators(); }, 15000);
