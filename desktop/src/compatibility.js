const compatibilityLabels = { pass: '检查通过', warning: '需要关注', error: '需要处理', info: '说明' };

function compatibilityCard() {
  const report = state.diagnostics?.compatibility;
  const issues = (report?.checks || []).filter(c => ['error', 'warning'].includes(c.status));
  const summary = report ? report.status === 'pass' ? '环境正常' : compatibilityLabels[report.status] || '待检查' : '尚未检查';
  return `<div class="preference-row compat-overview"><div class="preference-label"><div>Claude Science ${help('Science 兼容性', '检查实际启动版本、运行资源、启动接口和连接器依赖，不启动或停止研究任务。应用包版本可能落后于自动更新版本，以实际启动版本为准。本机资源检查通过不代表远程数据源或模型已验证。')}</div><span>${esc(report?.science_version || '未识别版本')} <span class="inline-status ${report?.status === 'pass' ? 'ok' : issues.length ? 'attention' : ''}">${esc(summary)}</span></span></div><div class="actions"><button class="btn" id="checkCompatibility">重新检查</button>${report ? `<button class="icon-btn" id="exportCompatibility" aria-label="导出检查报告" title="导出检查报告">${icon('download')}</button>` : ''}</div></div>
    ${report ? `${issues.length ? `<div class="compat-issues">${issues.map(c => `<div><strong>${esc(c.label)}</strong><p>${esc(c.detail)} ${esc(c.action)}</p></div>`).join('')}</div>` : ''}
      <details class="compat-details" ${state.compatibilityExpanded ? 'open' : ''}><summary>查看检查详情 ${icon('chevron-right')}</summary><div class="compat-facts"><span>应用包 <strong>${esc(report.bundle_version || '未识别')}</strong></span><span>连接器资源 <strong>${esc(report.available_profiles)} / ${esc(report.total_profiles)}</strong></span></div>
      <dl class="compat-checks">${report.checks.map(c => `<div class="compat-check" data-status="${esc(c.status)}"><dt>${icon(c.status === 'pass' ? 'check' : c.status === 'error' ? 'circle-x' : c.status === 'warning' ? 'triangle-alert' : 'info')}${esc(c.label)}</dt><dd>${esc(c.detail)}${c.action ? help(c.label, c.action) : ''}</dd></div>`).join('')}</dl>
      <details class="compat-paths"><summary>本机路径</summary><code>${esc(report.binary)}</code><code>${esc(report.runtime_path || '未找到运行资源')}</code></details><div class="compat-detail-footer"><button class="text-btn" id="compatibilityMcp">查看 MCP 状态</button><span>检查于 ${esc(new Date(report.checked_at).toLocaleString('zh-CN'))}</span>${help('检查报告', '导出报告包含版本和检查结果，不含密钥、对话和本机路径，不会自动上传。')}</div></details>` : `<p class="inline-warning">${esc(state.diagnostics?.compatibility_error || '请在桌面应用中检查本机环境。')}</p>`}`;
}

function bindCompatibility() {
  $('#checkCompatibility').onclick = () => action(async () => {
    notice('正在检查 Science 版本和本机资源…');
    state.diagnostics = await call('diagnostics');
    renderSettings();
    notice(state.diagnostics.compatibility ? '本机兼容性检查完成。' : state.diagnostics.compatibility_error || '检查未完成，请重试。', !state.diagnostics.compatibility);
  });
  const report = state.diagnostics?.compatibility;
  if (!report) return;
  $('.compat-details').ontoggle = e => { if (e.target.isConnected) state.compatibilityExpanded = e.target.open; };
  $('#exportCompatibility').onclick = () => {
    const { checked_at, baseline, science_version, bundle_version, runtime_version, available_profiles, total_profiles, status, checks } = report;
    download('csswitch-compatibility.json', JSON.stringify({ schema: 1, csswitch_version: state.diagnostics.version,
      checked_at, baseline, science_version, bundle_version, runtime_version, available_profiles, total_profiles, status, checks }, null, 2), 'application/json');
    notice('已导出版本和检查结果。');
  };
  $('#compatibilityMcp').onclick = () => showView('mcp');
}

async function launchScience(openOnly = false) {
  const phases = { gateway: '正在检查本地网关', prepare: '正在准备 Science 配置', science: '正在启动 Science 服务', connectors: '正在接入本机连接器', page: '正在打开 Science 页面' };
  let phase = openOnly ? phases.page : '正在连接本地服务';
  const started = Date.now();
  const paint = () => {
    $('#launchPhase').textContent = phase;
    const seconds = Math.floor((Date.now() - started) / 1000);
    $('#launchElapsed').textContent = `已等待 ${seconds} 秒${seconds >= 20 ? ' · 首次启动和连接器初始化可能较慢' : ''}`;
  };
  $('#launchHelp').hidden = true;
  $('#launchProgress').hidden = false;
  paint();
  const timer = setInterval(paint, 1000);
  let unlisten;
  try {
    // Listener failure must not prevent startup (e.g. older desktop capability files).
    try {
      unlisten = await window.__TAURI__?.event?.listen('science-launch-progress', event => {
        if (phases[event.payload?.phase]) { phase = phases[event.payload.phase]; paint(); }
      });
    } catch { /* Elapsed time still provides feedback. */ }
    const result = openOnly ? (await call('open_url'), { msg: '已打开正在运行的 Science。' }) : await call('one_click_login');
    notice([result.msg || 'Claude Science 已就绪。', result.mcp_notice].filter(Boolean).join(' '));
    await refreshRuntime();
  } catch (error) {
    $('#launchHelp').hidden = false;
    await refreshRuntime().catch(() => {});
    throw error;
  } finally {
    clearInterval(timer);
    if (unlisten) { try { unlisten(); } catch { /* Startup result takes priority. */ } }
    $('#launchProgress').hidden = true;
  }
}
