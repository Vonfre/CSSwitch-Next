const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const icon = (name) => `<i data-lucide="${name}"></i>`;
const protocolNames = { anthropic: 'Anthropic Messages', openai: 'OpenAI Chat', responses: 'OpenAI Responses' };
const templates = {
  custom: { name: '', protocol: 'anthropic', base_url: '', model: '', fast_model: '' },
  deepseek: { name: 'DeepSeek', protocol: 'anthropic', base_url: 'https://api.deepseek.com/anthropic', model: 'deepseek-v4-pro', fast_model: 'deepseek-v4-flash' },
  qwen: { name: '通义千问', protocol: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', fast_model: 'qwen-turbo' },
  openrouter: { name: 'OpenRouter', protocol: 'openai', base_url: 'https://openrouter.ai/api/v1', model: '', fast_model: '' },
  openai: { name: 'OpenAI', protocol: 'responses', base_url: 'https://api.openai.com/v1', model: '', fast_model: '' },
  anthropic: { name: 'Anthropic', protocol: 'anthropic', base_url: 'https://api.anthropic.com', model: '', fast_model: '' },
  ollama: { name: 'Ollama', protocol: 'openai', base_url: 'http://127.0.0.1:11434/v1', model: '', fast_model: '' },
};
const templateIcons = { custom: 'sliders-horizontal', deepseek: 'sparkles', qwen: 'shapes', openrouter: 'route', openai: 'aperture', anthropic: 'asterisk', ollama: 'hard-drive' };
const state = { config: null, runtime: { proxy: 'amber', sandbox: 'amber' }, view: 'workbench', selected: null,
  draft: null, dirty: false, search: '', filter: 'all', sort: 'active', busy: false, usage: [], logs: { proxy: '', sandbox: '' }, logTab: 'proxy', verified: {}, diagnostics: null, catalogs: {}, catalogAttempts: new Set(), editorTab: 'connection', preset: 'custom', modelTarget: null, catalogRequest: 0, catalogBusy: false, testing: null, draftTest: null };
const native = () => Boolean(window.__TAURI__?.core?.invoke);
const icons = () => window.lucide?.createIcons();
const fmt = (n) => new Intl.NumberFormat('zh-CN').format(n || 0);

async function call(command, args = {}) {
  if (!native()) throw new Error('请在 CSSwitch Next 桌面应用中执行此操作。');
  return window.__TAURI__.core.invoke(command, args);
}

function notice(message, error = false) {
  const node = $('#notice');
  node.textContent = String(message);
  node.classList.toggle('error', error);
  node.hidden = !message;
  const editorNotice = $('#editorNotice');
  if (editorNotice) { editorNotice.textContent = error ? String(message) : ''; editorNotice.hidden = !error || !message; }
}

async function confirmAction(title, body) {
  const dialog = $('#confirmDialog');
  $('#confirmTitle').textContent = title;
  $('#confirmBody').textContent = body;
  dialog.returnValue = 'cancel';
  dialog.showModal();
  return new Promise((resolve) => dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), { once: true }));
}

async function action(fn) {
  if (state.busy) return;
  state.busy = true;
  document.body.classList.add('busy');
  $('#content').setAttribute('aria-busy', 'true');
  $('#content').inert = true;
  $('#refreshBtn').classList.add('spin');
  try {
    const pending = fn();
    if ($('#providerDialog').open) $('#editor').inert = true;
    await pending;
  } catch (e) { notice(String(e.message || e), true); }
  finally {
    state.busy = false;
    $('#refreshBtn').classList.remove('spin');
    document.body.classList.remove('busy');
    $('#content').setAttribute('aria-busy', 'false');
    $('#content').inert = false;
    $('#editor').inert = false;
  }
}

async function loadConfig() {
  state.config = await call('get_config');
  if (!state.selected) state.selected = state.config.provider;
}

async function refreshRuntime() {
  if (!native()) return;
  const previousProxy = state.runtime.proxy;
  state.runtime = await call('status');
  renderRuntime();
  if (previousProxy !== state.runtime.proxy && state.view === 'workbench') renderList();
}

function active() { return state.config?.connections.find((p) => p.id === state.config.provider); }
function selected() { return state.draft || state.config?.connections.find((p) => p.id === state.selected); }
function readDraft() {
  const form = $('#connectionForm');
  if (!form) return null;
  const data = Object.fromEntries(new FormData(form));
  return { ...data, id: state.selected, max_tokens: Number(data.max_tokens), key: data.key || null };
}

function host(url) { try { return new URL(url).host; } catch { return url || ''; } }
function mark(p) {
  const match = Object.entries(templates).find(([id, t]) => id !== 'custom' && host(t.base_url) === host(p.base_url));
  return `<span class="provider-mark ${esc(p.protocol)}">${icon(templateIcons[match?.[0] || 'custom'])}</span>`;
}
function testSignature(input) {
  return JSON.stringify([input.protocol, input.base_url.trim().replace(/\/+$/, ''), input.model, input.quality_model || '', input.fast_model || '', Number(input.max_tokens)]);
}

function renderRuntime() {
  const box = $('#runtimeSection');
  if (!box || !state.config) return;
  const p = active();
  const running = state.runtime.sandbox === 'green';
  const gateway = state.runtime.proxy === 'green';
  const verify = state.verified[p?.id];
  box.innerHTML = `<div class="runtime"><div class="runtime-identity"><span class="science-mark">${icon('flask-conical')}</span><div><div class="runtime-title">Claude Science <span class="badge ${running && gateway ? 'active' : ''}">${running && gateway ? '运行中' : running ? '网关已停止' : '未启动'}</span></div><div class="runtime-sub"><strong>${esc(p?.name || '未选择提供商')}</strong><span>${esc(p?.model || '未选择模型')}</span></div></div></div><div class="actions"><button class="btn primary" id="startBtn">${icon(running && gateway ? 'external-link' : 'play')}${running && gateway ? '打开 Science' : '一键开始'}</button><button class="icon-btn outlined" id="stopBtn" title="停止网关与 Science" aria-label="停止网关与 Science">${icon('square')}</button></div></div>
    <div class="health"><span><span class="dot ${gateway ? 'green' : ''}"></span>网关 ${gateway ? '就绪' : '未启动'}</span><span><span class="dot ${running ? 'green' : ''}"></span>Science ${running ? '就绪' : '未启动'}</span><span><span class="dot ${verify ? verify.ok ? 'green' : 'red' : ''}"></span>模型 ${verify ? verify.ok ? '测试通过' : '测试失败' : '未测试'}</span><span class="history-label">${icon('folder-open')}官方对话目录</span></div>`;
  $('#stopBtn').disabled = !gateway && !running;
  $('#startBtn').disabled = !p || !p.has_key;
  if (!p) $('#startBtn').title = '请先添加并选择提供商';
  $('#startBtn').onclick = () => action(async () => {
    if (state.dirty) { notice('连接有未保存的修改，请先保存。', true); return; }
    if (native() && !running && !await confirmAction('启动第三方 Science？', '将使用原有对话记录并重启本机 Science 服务。第三方模式免登录。请先结束研究任务，并关闭原来的 Science 窗口。')) return;
    notice('正在启动本地网关与 Claude Science...');
    const result = await call('one_click_login');
    notice([result.msg || 'Claude Science 已就绪。', result.mcp_notice].filter(Boolean).join(' '));
    await refreshRuntime();
  });
  $('#stopBtn').onclick = () => action(async () => {
    if (!await confirmAction('停止 Science？', '正在运行的研究任务会中断，对话记录保留。')) return;
    await call('stop_all'); notice('网关与共享 Science 已停止。'); await refreshRuntime();
  });
  icons();
}


function download(name, data, type) {
  const url = URL.createObjectURL(new Blob([data], { type }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderLogs() {
  $('#content').innerHTML = `<div class="log-toolbar"><div class="segmented" role="group" aria-label="日志来源"><button data-log="proxy" class="${state.logTab === 'proxy' ? 'active' : ''}">本地网关</button><button data-log="sandbox" class="${state.logTab === 'sandbox' ? 'active' : ''}">Science</button></div><div class="actions"><button class="icon-btn" id="downloadLogs" title="下载当前日志" aria-label="下载当前日志">${icon('download')}</button><button class="btn" id="folderBtn">${icon('folder-open')}打开日志目录</button></div></div><pre id="logText"></pre>`;
  $('#logText').textContent = state.logs[state.logTab] || '暂无日志。';
  document.querySelectorAll('[data-log]').forEach((b) => { b.onclick = () => { state.logTab = b.dataset.log; renderLogs(); }; });
  $('#folderBtn').onclick = () => action(() => call('open_logs'));
  $('#downloadLogs').disabled = !state.logs[state.logTab];
  $('#downloadLogs').onclick = () => download(`csswitch-${state.logTab}.log`, state.logs[state.logTab], 'text/plain');
  icons();
}

function renderSettings() {
  const d = state.diagnostics;
  $('#content').innerHTML = `<section class="settings-section" id="updateCard"></section><section class="settings-section"><h2>Science 插件</h2><div class="diagnostic-row"><div><strong>使用本机 MCP</strong><p class="hint">第三方模式接入 24 个本机连接器，其中 4 个托管服务改用本地版本。连接器名称保留原文，工具仍由 Science 审批。</p></div><button class="switch" role="switch" id="localMcpToggle" aria-label="使用本机 MCP" aria-checked="${state.config.science_local_mcp !== false}"><span></span></button></div><div class="diagnostic-row"><div><strong>Science 页面汉化</strong><p class="hint">通过 CSSwitch 打开的页面使用本地词库汉化导航、菜单、按钮与提示，支持动态页面和即时关闭。对话正文、代码、输入内容保持原样；未收录的文案保留英文。</p></div><button class="switch" role="switch" id="scienceZhToggle" aria-label="Science 页面汉化" aria-checked="${Boolean(state.config.science_zh)}"><span></span></button></div><p class="hint">已在浏览器中打开的旧页面，请从提供商页面重新点击「打开 Science」。官方客户端窗口不加载此插件。</p></section><section class="settings-section"><h2>本地服务端口</h2><form id="settingsForm"><div class="form-grid"><div class="field"><label for="proxyPort">网关端口</label><input id="proxyPort" name="proxy_port" type="number" min="1024" max="65535" value="${state.config.proxy_port}" required /></div><div class="field"><label for="sandboxPort">Science 端口</label><input id="sandboxPort" name="sandbox_port" type="number" min="1024" max="65535" value="${state.config.sandbox_port}" required /></div></div><p class="hint">更改端口需要先停止服务。Science 可使用原端口 8765。</p><div class="actions"><button class="btn primary" type="submit">${icon('save')}保存端口</button></div></form></section><section class="settings-section" id="environmentCard"><h2>环境检查</h2>${d ? [['Claude Science', d.science_installed ? '已安装' : '未找到'], ['Python 3', d.python || '未找到'], ['配置目录', d.config_dir], ['对话数据目录', d.science_data_dir], ['本地认证目录', d.science_auth_dir], ['旧隔离目录（保留）', d.legacy_sandbox_home], ['应用版本', `${d.version} · ${d.platform}`]].map(([label, value]) => `<div class="diagnostic-row"><span>${esc(label)}</span><code>${esc(value)}</code></div>`).join('') : '<div class="empty">请在桌面应用中检查运行环境。</div>'}</section><section class="settings-section"><h2>本地存储</h2><p class="hint">API Key 以明文保存在配置文件中，权限为 0600。用量记录不保存对话内容。验证连接会向所选 API 发送一个最小请求，可能产生少量费用。</p></section>`;
  const feature = (key) => action(async () => {
    const local_mcp = key === 'local' ? state.config.science_local_mcp === false : state.config.science_local_mcp !== false;
    const zh = key === 'zh' ? !state.config.science_zh : Boolean(state.config.science_zh);
    const result = await call('set_science_features', { localMcp: local_mcp, zh });
    await loadConfig(); notice(result.message); renderSettings();
  });
  $('#localMcpToggle').onclick = () => feature('local');
  $('#scienceZhToggle').onclick = () => feature('zh');
  $('#settingsForm').onsubmit = (e) => { e.preventDefault(); action(async () => {
    const values = Object.fromEntries(new FormData(e.target));
    const proxy_port = Number(values.proxy_port), sandbox_port = Number(values.sandbox_port);
    if (proxy_port === sandbox_port || proxy_port === 8765) { notice('端口不能相同，网关不能使用 Science 保留端口 8765。', true); return; }
    if (proxy_port === state.config.proxy_port && sandbox_port === state.config.sandbox_port) { notice('端口未变更。'); return; }
    if (state.runtime.proxy === 'green' || state.runtime.sandbox === 'green') {
      if (!await confirmAction('停止服务并更改端口？', '正在运行的研究任务会中断，对话记录保留。')) return;
      await call('stop_all');
    }
    await call('set_config', { cfg: { provider: state.config.provider, proxy_port, sandbox_port } });
    await loadConfig(); await refreshRuntime(); notice('端口已保存，可返回工作台重新启动。'); renderSettings();
  }); };
  if (d?.science_history) {
    const row = document.createElement('div'); row.className = 'diagnostic-row';
    row.innerHTML = `<span>本地历史总计</span><code>${fmt(d.science_history.projects)} 项目 · ${fmt(d.science_history.conversations)} 主会话 · ${fmt(d.science_history.messages)} 消息</code>`;
    $('#environmentCard').append(row);
  }
  renderUpdater();
  icons();
}

async function showView(view) {
  if (state.busy || view === state.view) return;
  if (state.dirty && view !== state.view) {
    if (!await confirmAction('放弃未保存的修改？', '离开工作台会丢弃当前表单的修改。')) return;
    state.dirty = false; state.draft = null; state.selected = state.config.provider;
  }
  state.view = view;
  document.body.dataset.view = view;
  document.querySelectorAll('.nav-item').forEach(b => {
    b.classList.toggle('active', b.dataset.view === view);
    if (b.dataset.view === view) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  $('#pageTitle').textContent = { workbench: '提供商', mcp: 'MCP 管理', skills: 'Skills 管理', sessions: '会话管理', usage: '用量统计', logs: '运行日志', settings: '设置' }[view];
  if (['mcp', 'skills', 'sessions'].includes(view)) {
    $('#content').innerHTML = '<div class="empty">正在读取管理数据…</div>';
    await action(async () => { await ({ mcp: loadMcp, skills: loadSkills, sessions: loadSessions })[view](); ({ mcp: renderMcp, skills: renderSkills, sessions: renderSessions })[view](); }); return;
  }
  await action(async () => {
    if (native()) {
      if (view === 'usage') await loadUsage();
      if (view === 'logs') state.logs = await call('read_logs');
      if (view === 'settings') state.diagnostics = await call('diagnostics');
    }
    ({ workbench: renderWorkbench, usage: renderUsage, logs: renderLogs, settings: renderSettings })[view]();
  });
}

document.querySelectorAll('.nav-item[data-view]').forEach((b) => { b.onclick = () => showView(b.dataset.view); });
function applySidebarMode() {
  const collapsed = document.documentElement.dataset.sidebar === 'collapsed';
  $('#sidebarToggle').setAttribute('aria-label', collapsed ? '展开导航' : '收起导航');
  $('#sidebarToggle').setAttribute('aria-expanded', String(!collapsed));
  document.querySelectorAll('.nav-item').forEach(b => { b.title = b.textContent.trim(); });
}
document.documentElement.dataset.sidebar = localStorage.getItem('csswitch-sidebar') || 'expanded';
applySidebarMode();
$('#sidebarToggle').onclick = () => {
  const value = document.documentElement.dataset.sidebar === 'collapsed' ? 'expanded' : 'collapsed';
  document.documentElement.dataset.sidebar = value; localStorage.setItem('csswitch-sidebar',value); applySidebarMode();
};
$('#themeBtn').onclick = () => {
  const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme; localStorage.setItem('csswitch-theme', theme);
};
document.documentElement.dataset.theme = localStorage.getItem('csswitch-theme') || 'light';
$('#workspaceDialog').oncancel = e => { if (state.busy) e.preventDefault(); };
$('#workspaceDialog').onclose = () => { if (!$('#workspaceDialog').open) $('#workspaceDialog').innerHTML = ''; };
$('#quitBtn').onclick = () => action(() => call('quit_app'));
$('#refreshBtn').onclick = () => action(async () => {
  if (state.dirty) { notice('请先保存连接配置，再刷新。', true); return; }
  await loadConfig(); await refreshRuntime();
  if (['mcp', 'skills', 'sessions'].includes(state.view)) { await ({ mcp: loadMcp, skills: loadSkills, sessions: loadSessions })[state.view](state.view === 'sessions'); ({ mcp: renderMcp, skills: renderSkills, sessions: renderSessions })[state.view](); return; }
  if (state.view === 'usage') await loadUsage();
  if (state.view === 'logs') state.logs = await call('read_logs');
  if (state.view === 'settings') state.diagnostics = await call('diagnostics');
  ({ workbench: renderWorkbench, usage: renderUsage, logs: renderLogs, settings: renderSettings })[state.view]();
});

async function init() {
  icons();
  try {
    if (native()) { await loadConfig(); await refreshRuntime(); }
    else {
      $('#previewLabel').hidden = false;
      state.config = { provider: '', mode: 'proxy', proxy_port: 18992, sandbox_port: 8991, connections: [] };
      state.selected = '';
    }
    $('#content').setAttribute('aria-busy', 'false'); renderWorkbench();
    initUpdater();
  } catch (e) { $('#content').innerHTML = '<div class="empty">无法读取配置，请检查本地配置文件。</div>'; notice(String(e), true); }
}
init();
setInterval(() => {
  if (native() && !state.busy && state.view === 'workbench' && !document.hidden) refreshRuntime().catch(() => {});
}, 8000);
