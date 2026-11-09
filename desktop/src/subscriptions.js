let subscriptions = null;
let subscriptionUsage = {};
let subscriptionUsageLoading = false;
let subscriptionUsageTarget = null;
let subscriptionUsageStarted = false;

// Adapted from CC Switch quotaRules.ts (MIT, Jason Young, 2025).
// See assets/cc-switch-LICENSE.txt and docs/THIRD_PARTY.md.
function quotaCountdown(resetsAt, now = Date.now()) {
  if (!resetsAt) return null;
  const diffMs = resetsAt * 1000 - now;
  if (!Number.isFinite(diffMs) || diffMs <= 0) return null;
  const hours = Math.floor(diffMs / 3600000);
  const minutes = Math.floor((diffMs % 3600000) / 60000);
  if (hours > 24) return `${Math.floor(hours / 24)}d${hours % 24}h`;
  if (hours > 0) return `${hours}h${minutes}m`;
  return `${minutes}m`;
}
function quotaBarPercent(value) {
  return Number.isFinite(value) ? Math.max(0, Math.min(100, value)) : 100;
}
function quotaRelativeTime(timestamp, now = Date.now()) {
  const diff = Math.floor((now - timestamp * 1000) / 1000);
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

function subscriptionQuotaMarkup(id) {
  const usage = subscriptionUsage[id];
  const stale = usage?.stale || usage?.status !== 'ok';
  const windows = usage?.windows?.length ? usage.windows : [{ key: 'five_hour', label: '5 小时' }, { key: 'seven_day', label: '一周' }];
  const rows = windows.map(w => {
    const known = Number.isFinite(w.remaining_percent);
    const remaining = known ? quotaBarPercent(w.remaining_percent) : null;
    const countdown = quotaCountdown(w.resets_at);
    const resetText = countdown ? `${countdown} 后重置` : w.resets_at ? '已到重置时间，等待更新' : '重置时间未提供';
    return `<div class="subscription-quota-window ${stale ? 'is-stale' : ''}" title="${esc(resetText)}">
      <span class="subscription-quota-label">${esc(w.label)}</span>
      ${known ? `<progress max="100" value="${remaining}" aria-label="${esc(w.label)}剩余额度" class="${remaining < 20 ? 'is-low' : ''}"></progress>` : '<div class="subscription-quota-track"></div>'}
      <strong class="subscription-quota-value">${known ? `${remaining.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}% 剩余` : '暂无数据'}</strong>
      <span class="subscription-quota-reset" aria-label="${esc(resetText)}">${icon('clock-3')}${countdown || '—'}<span class="sr-only">后重置</span></span></div>`;
  }).join('');
  const loading = subscriptionUsageLoading && (!subscriptionUsageTarget || subscriptionUsageTarget === id);
  const updated = usage?.fetched_at ? quotaRelativeTime(usage.fetched_at) : '未更新';
  const account = subscriptions?.accounts.find(a => a.id === id);
  const reauthorize = ['auth_required', 'credentials_unavailable'].includes(usage?.status) ? '<button class="btn subscription-quota-login" data-subscription-reauthorize>重新授权</button>' : '';
  return `<div class="subscription-quota-windows">${rows}</div><div class="subscription-quota-refresh"><time>${loading ? '更新中…' : esc(updated)}</time><button class="icon-btn ${loading ? 'is-loading' : ''}" data-quota-refresh="${esc(id)}" aria-label="刷新 ${esc(account?.user || id)} 的额度" ${subscriptionUsageLoading ? 'disabled' : ''}>${icon('refresh-cw')}</button></div>
    <div class="subscription-quota-status" role="status">${esc(usage?.message || '')}${stale && usage?.fetched_at ? '<span>上次读数</span>' : ''}${reauthorize}</div>`;
}

function updateSubscriptionQuotas() {
  if (state.view !== 'subscriptions') return;
  document.querySelectorAll('[data-subscription-quota]').forEach(el => {
    el.innerHTML = subscriptionQuotaMarkup(el.dataset.subscriptionQuota);
  });
  document.querySelectorAll('[data-subscription-reauthorize]').forEach(el => { el.onclick = () => subscriptionAction('login'); });
  document.querySelectorAll('[data-quota-refresh]').forEach(el => { el.onclick = () => refreshSubscriptionUsage(el.dataset.quotaRefresh); });
  icons();
  const refresh = $('#subscriptionUsageRefresh');
  if (refresh) { refresh.disabled = subscriptionUsageLoading; refresh.textContent = subscriptionUsageLoading ? '查询中…' : '刷新额度'; }
}

async function refreshSubscriptionUsage(id = null) {
  if (subscriptionUsageLoading || state.busy || !subscriptions?.accounts.length) return;
  subscriptionUsageLoading = true;
  subscriptionUsageTarget = id;
  updateSubscriptionQuotas();
  try {
    const result = await call('subscription_manage', { operation: 'usage', params: id ? { id, force: true } : { force: true } });
    if (!result.accounts || Array.isArray(result.accounts)) throw new Error('额度查询暂不可用，请稍后刷新。');
    subscriptionUsage = result.accounts;
  } catch (_) {
    for (const a of subscriptions.accounts.filter(a => !id || a.id === id)) subscriptionUsage[a.id] = { ...subscriptionUsage[a.id], status: 'unavailable', stale: true, message: '额度查询暂不可用，请稍后刷新。' };
  } finally {
    subscriptionUsageLoading = false;
    subscriptionUsageTarget = null;
    updateSubscriptionQuotas();
  }
}

// Update the displayed time locally; this timer never queries the service.
setInterval(() => { if (!document.hidden) updateSubscriptionQuotas(); }, 30000);

async function initializeSubscriptionUsage() {
  if (subscriptionUsageStarted) return;
  subscriptionUsageStarted = true;
  try {
    if (!subscriptions) await loadSubscriptions();
    await refreshSubscriptionUsage();
  } catch (_) { /* Manual refresh remains available if startup was offline. */ }
}

function closeSubscriptionMenus(except = null) {
  document.querySelectorAll('.subscription-account-menu[open]').forEach(menu => {
    if (menu !== except) menu.open = false;
  });
}
document.addEventListener('pointerdown', event => {
  closeSubscriptionMenus(event.target.closest('.subscription-account-menu'));
});
document.addEventListener('focusin', event => {
  closeSubscriptionMenus(event.target.closest('.subscription-account-menu'));
});
document.addEventListener('keydown', event => {
  const menu = document.querySelector('.subscription-account-menu[open]');
  if (event.key === 'Escape' && menu) {
    event.preventDefault();
    menu.open = false;
    menu.querySelector('summary').focus();
  }
});

async function loadSubscriptions() {
  subscriptions = await call('subscription_manage', { operation: 'status' });
}

async function subscriptionAction(operation, params = {}) {
  await action(async () => {
    const result = await call('subscription_manage', { operation, params });
    notice(result.message || '操作已完成。');
    await loadSubscriptions();
    renderSubscriptions();
  });
}

async function deleteSubscription(account) {
  if (state.busy) return;
  if (!await confirmAction('删除订阅账号？', `将删除 ${account.user} 在 CSSwitch 中的账号记录和登录缓存，再次使用需重新授权。Science 项目、会话和历史备份保留，不会取消官方订阅。`)) return;
  await subscriptionAction('delete', { id: account.id });
}

function renderSubscriptions() {
  if (!subscriptions) return;
  const s = subscriptions;
  if (s.hot_mode) return renderHotSubscriptions(s);
  const native = s.native || {};
  const nativeMode = !!native.enabled;
  const current = s.accounts.find(a => a.id === native.account_id);
  const running = !!s.science.running;
  const authenticated = running && native.authenticated !== false;
  const issue = native.last_switch?.phase && !['complete', 'rolled_back'].includes(native.last_switch.phase);
  const planName = value => ({ pro: 'Pro', max: 'Max', team: 'Team', enterprise: 'Enterprise' }[value] || value || '订阅');
  const accountRow = a => {
    const selected = a.id === native.account_id;
    const label = selected ? (authenticated ? 'Science 已登录' : running ? '登录已失效，请重新授权' : 'Science 已配置') : a.lapsed ? '需要重新登录' : '可切换';
    const deleteReason = issue ? '请先恢复未完成的账号切换，再删除。' : selected ? '请先切换到其他账号，再删除当前 Science 账号。' : '删除账号';
    return `<article class="subscription-account ${selected ? 'is-current' : ''}">
      <div class="subscription-avatar" aria-hidden="true">${esc((a.user || '?').slice(0, 1).toUpperCase())}</div>
      <div class="subscription-account-info"><div class="subscription-account-name"><strong title="${esc(a.user)}">${esc(a.user)}</strong><span class="subscription-plan">${esc(planName(a.plan))}</span></div>
        <div class="subscription-account-meta"><span class="subscription-account-state ${selected ? 'is-current' : ''} ${a.lapsed ? 'is-error' : ''}">${icon(a.lapsed ? 'circle-alert' : selected ? 'circle-check' : 'circle')}${label}</span></div>
      </div><div class="subscription-quota" data-subscription-quota="${esc(a.id)}">${subscriptionQuotaMarkup(a.id)}</div>
      <div class="subscription-account-actions"><button class="btn subscription-account-action" ${a.lapsed || (selected && running && !authenticated) ? 'data-subscription-login' : `data-subscription-id="${esc(a.id)}"`} ${selected && authenticated || issue ? 'disabled' : ''}>${icon(selected && authenticated ? 'check' : a.lapsed ? 'log-in' : 'arrow-right-left')}${a.lapsed || (selected && running && !authenticated) ? '重新登录' : selected && authenticated ? '已登录' : '切换登录'}</button>
      <span title="${deleteReason}"><button class="btn subscription-account-action subscription-account-delete" data-subscription-delete="${esc(a.id)}" aria-label="删除账号 ${esc(a.user)}" ${selected || issue ? 'disabled' : ''}>${icon('trash-2')}删除</button></span></div>
    </article>`;
  };
  $('#pageTitle').innerHTML = `${icon('users-round')}订阅账号`;
  $('#content').innerHTML = `<section class="subscription-panel subscription-compact" aria-label="订阅账号管理">
    <section class="subscription-card" aria-labelledby="subscriptionClaudeTitle">
      <header class="subscription-card-header"><div class="subscription-provider"><span class="subscription-provider-mark" aria-hidden="true">${icon('sparkles')}</span><div><div class="subscription-title-line"><h2 id="subscriptionClaudeTitle">Claude</h2><span class="subscription-label">官方订阅</span></div><p>切换 Science 真实登录，保留本地项目与会话。</p></div></div><button class="btn primary" id="subscriptionLogin">${icon('plus')}添加账号</button></header>
      ${s.login && s.login.status !== 'success' ? `<div class="subscription-login-status ${s.login.status === 'error' ? 'is-error' : ''}" role="status">${icon(s.login.status === 'error' ? 'circle-alert' : 'clock-3')}<div><strong>${s.login.status === 'error' ? '上次登录未完成' : '等待官方授权'}</strong><p>${esc(s.login.message)}</p></div></div>` : ''}
      <div class="subscription-list-label"><span>账号 <span class="subscription-count">${s.accounts.length}</span> · Usage limits</span><span>${s.accounts.length} 个已保存 <button class="btn" id="subscriptionUsageRefresh" ${subscriptionUsageLoading || !s.accounts.length ? 'disabled' : ''}>${subscriptionUsageLoading ? '查询中…' : '刷新额度'}</button></span></div>
      <p class="subscription-quota-note">启动时更新 · 点击刷新查看最新额度</p>
      <div class="subscription-accounts">${s.accounts.map(accountRow).join('') || `<div class="subscription-empty"><span class="subscription-empty-icon">${icon('user-round-plus')}</span><strong>尚未登录 Claude 订阅账号</strong><p>添加你的 Pro、Max 或 Team 账号，即可在这里管理。</p></div>`}</div>
      <footer class="subscription-policy"><div class="subscription-policy-info"><span class="subscription-policy-icon">${icon('arrow-right-left')}</span><div><div class="subscription-policy-title"><strong>切换方式</strong><span class="subscription-mode">原生登录</span></div><p>切换会短暂重启 Science。当前对话会中断，完成后在原会话点击 Resume。</p></div></div></footer>
    </section>
    <section class="subscription-card subscription-science" aria-labelledby="subscriptionScienceTitle">
      <div class="subscription-science-main"><span class="subscription-science-mark" aria-hidden="true">${icon('flask-conical')}</span><div class="subscription-science-info"><div class="subscription-title-line"><h2 id="subscriptionScienceTitle">Claude Science</h2><span class="subscription-connection ${running ? 'is-connected' : ''}"><span class="dot ${running ? 'green' : 'amber'}"></span>${running ? '运行中' : s.installed ? '未启动' : '未安装'}</span></div><p>${nativeMode ? '使用所选账号的真实官方登录，模型请求直接访问官方服务。' : '添加官方订阅账号后，可切换 Science 原生登录。'}</p></div></div>
      <div class="subscription-routing" aria-label="Science 实际登录">
        <div><span>${running ? '当前登录' : '已配置账号'}</span><strong>${esc(current?.user || '尚未识别到已添加的账号')}</strong></div>
        <div><span>会话接续</span><strong>${issue ? '切换未完成，需恢复' : native.last_switch?.phase === 'complete' ? (native.last_switch.history_transferred === false ? '旧版切换未共享会话，请重新切换' : '本地会话已保留') : '切换前自动备份'}</strong>${native.last_switch?.phase === 'complete' ? `<time>${esc(new Date(native.last_switch.at * 1000).toLocaleString('zh-CN'))}</time>` : ''}</div>
        <p>${issue ? `原数据已保留。备份目录：${esc(native.backup_dir || '订阅目录 native/backups')}` : native.initial_login_required ? '请先在官方 Science 完成一次登录并打开原会话，然后在这里添加订阅账号、切换登录。现有对话不会被清空。' : native.last_switch?.history_transferred === false ? '上次为旧版切换，未共享会话。使用修复版再次切换后，会合并并校验两侧历史。' : '保留原会话 ID、历史消息和工具结果。Resume 由你在 Science 中触发，避免自动重跑计算。'}</p>${issue ? '<button class="btn" id="subscriptionRecover">恢复原账号</button>' : ''}
        ${s.data_dir ? `<div><span>本机对话目录</span><strong style="overflow-wrap:anywhere">${esc(s.data_dir)}</strong><span>${s.history_found ? '已发现本地会话库' : '尚未发现会话库，请在 Science 中确认数据目录'}</span></div>` : ''}
      </div>
      <div class="subscription-service-bar"><span><span class="dot ${nativeMode ? 'green' : 'amber'}"></span>${nativeMode ? 'Science 管理登录凭据' : '等待配置原生订阅'}</span><span>${nativeMode ? '订阅网关不参与推理' : '点击账号的「切换登录」完成配置'}</span></div>
    </section>
    <details class="subscription-help"><summary>${icon('circle-help')}切换与 Resume 说明${icon('chevron-down')}</summary><div><p>切换先停止 Science 并备份登录及两侧会话，将目标账号已有历史合入当前会话副本，校验完整消息后切换。保留 Python 环境中的符号链接。启动后同时核对 Science 的登录状态及官方账号接口；失效授权不会显示为切换成功。</p><p>支持本机 Science ${esc(native.supported_versions || '0.1.72')}，切换前还会检查实际凭据和数据格式。Resume 保留对话上下文；内存中的 Python/R 对象、仍运行的本地进程、远程任务或账号专属云端资源能否恢复，取决于 Science 自身能力。</p></div></details>
  </section>`;
  $('#subscriptionLogin').onclick = () => subscriptionAction('login');
  if (s.hot_supported !== false && s.accounts.length >= 2) {
    const panel = document.createElement('div');
    panel.className = 'subscription-policy';
    panel.innerHTML = '<div><strong>免重启推理切换</strong><p>首次接入重启一次，之后在同一对话中切换推理账号；额度不足时可自动接管。Science 自身登录身份保持不变。</p></div><button class="btn primary" id="subscriptionHotEnable">启用免重启切换</button>';
    $('#subscriptionClaudeTitle').closest('section').append(panel);
    $('#subscriptionHotEnable').onclick = () => subscriptionAction('hot_enable');
  }
  $('#subscriptionUsageRefresh').onclick = () => refreshSubscriptionUsage();
  if ($('#subscriptionRecover')) $('#subscriptionRecover').onclick = () => subscriptionAction('recover');
  document.querySelectorAll('[data-subscription-id]').forEach(b => {
    b.onclick = () => subscriptionAction('switch', { id: b.dataset.subscriptionId });
  });
  document.querySelectorAll('[data-subscription-login]').forEach(b => {
    b.onclick = () => subscriptionAction('login');
  });
  document.querySelectorAll('[data-subscription-delete]').forEach(b => {
    b.onclick = () => deleteSubscription(s.accounts.find(a => a.id === b.dataset.subscriptionDelete));
  });
  updateSubscriptionQuotas();
  icons();
}

async function syncScienceLogin(id) {
  const account = subscriptions?.accounts.find(a => a.id === id);
  if (!account || state.busy) return;
  if (!await confirmAction('同步 Science 登录邮箱？', `将真实登录切换为 ${account.user}，Science 需要重启一次，完成后保留免重启推理切换。项目与会话会先备份并校验；有任务运行时不会执行。`)) return;
  await subscriptionAction('sync_login', { id });
}

function renderHotSubscriptions(s) {
  const nativeAccount = s.accounts.find(a => a.id === s.native?.account_id);
  const selected = s.accounts.find(a => a.id === s.next_account_id) || s.accounts.find(a => a.current);
  const preferred = s.mode === 'auto' ? (s.preferred_id === undefined ? s.accounts.find(a => a.current)?.id : s.preferred_id) : null;
  const last = s.accounts.find(a => a.id === s.last_inference?.account_id);
  const preferredAccount = s.accounts.find(a => a.id === preferred);
  const loginDiffers = preferredAccount && preferredAccount.id !== nativeAccount?.id;
  const connected = s.connected && s.gateway && s.science.running;
  $('#pageTitle').innerHTML = `${icon('users-round')}订阅账号`;
  $('#content').innerHTML = `<section class="subscription-panel subscription-compact" aria-label="订阅账号管理">
    <section class="subscription-card">
      <header class="subscription-card-header"><div class="subscription-provider"><span class="subscription-provider-mark">${icon('sparkles')}</span><div class="subscription-title-line"><h2>Claude</h2><span class="subscription-label" title="更换使用的订阅账号，无需重启 Science">切换无需重启</span></div></div><button class="btn" id="subscriptionLogin">${icon('plus')}添加账号</button></header>
      ${s.login && s.login.status !== 'success' ? `<div class="subscription-login-status ${s.login.status === 'error' ? 'is-error' : ''}" role="status"><div><strong>${s.login.status === 'error' ? '上次登录未完成' : '等待官方授权'}</strong><p>${esc(s.login.message)}</p></div></div>` : ''}
      <div class="subscription-list-label"><span>Usage limits · ${s.accounts.length} 个账号</span><button class="btn" id="subscriptionUsageRefresh">刷新额度</button></div>
      <p class="subscription-quota-note">启动时更新 · 点击刷新查看最新额度</p>
      <div class="subscription-accounts">${s.accounts.map(a => `<article class="subscription-account ${a.current ? 'is-current' : ''}">
        <div class="subscription-avatar" aria-hidden="true">${esc(a.user.slice(0,1).toUpperCase())}</div>
        <div class="subscription-account-info"><div class="subscription-account-name"><strong title="${esc(a.user)}">${esc(a.user)}</strong></div>
          <div class="subscription-account-meta"><span class="subscription-plan">${esc(({pro:'Pro',max:'Max',team:'Team'})[a.plan] || a.plan || '订阅')}</span><span>${a.lapsed ? '需要重新授权' : '已保存'}${a.cooldown_until > Date.now()/1000 ? ' · 冷却中' : ''}</span></div></div>
        <div class="subscription-quota" data-subscription-quota="${esc(a.id)}">${subscriptionQuotaMarkup(a.id)}</div>
        <div class="subscription-row-controls"><label class="subscription-priority-control"><span>优先消耗</span><button type="button" class="switch" role="switch" aria-checked="${a.id === preferred}" aria-label="优先消耗 ${esc(a.user)}" data-hot-priority="${esc(a.id)}" ${!connected || (a.lapsed && a.id !== preferred) ? 'disabled' : ''}><span></span></button></label>
        <details class="subscription-account-menu"><summary aria-label="管理账号 ${esc(a.user)}" title="管理账号">${icon('ellipsis')}</summary><div class="subscription-menu-items" aria-label="账号操作">
          <button type="button" data-subscription-login>${icon('key-round')}重新授权</button>
          <button type="button" class="is-danger" data-subscription-delete="${esc(a.id)}" ${a.id === s.native?.account_id ? 'disabled' : ''} title="${a.id === s.native?.account_id ? '请先同步登录到其他账号，再删除此账号' : '移除保存的账号'}">${icon('trash-2')}删除账号</button>
        </div></details></div>
      </article>`).join('')}</div>
      <footer class="subscription-policy"><div><strong>${s.mode === 'auto' ? '自动使用备用账号' : '仅使用当前账号'}</strong><p>${preferred ? '优先使用开关选中的账号；不可用时自动使用其他账号。' : s.mode === 'auto' ? '未指定优先账号，沿用当前账号；不可用时自动使用其他账号。' : '开启优先消耗后，可在账号不可用时自动使用其他账号。'}正在进行的回复不受影响。</p></div>${s.mode !== 'auto' ? `<button class="btn" id="subscriptionHotAuto" ${!connected ? 'disabled' : ''}>开启自动切换</button>` : ''}</footer>
    </section>
    <section class="subscription-card subscription-science"><div class="subscription-science-main"><span class="subscription-science-mark">${icon('flask-conical')}</span><div class="subscription-science-info"><h2>Claude Science</h2><p>同一窗口、同一对话，保留当前工作区。</p></div></div>
      <div class="subscription-routing"><div><span>路由连接</span><strong>${connected ? '已连接 · 切换无需重启' : '尚未连接，请恢复接入'}</strong></div>
      <div><span>下一次回复使用</span><strong>${esc(selected?.user || '未选择')}</strong></div>
      <div><span>最近回复使用</span><strong>${esc(last?.user || '暂无回复记录')}</strong>${s.last_inference?.completed_at ? `<time>${new Date(s.last_inference.completed_at*1000).toLocaleTimeString('zh-CN')}</time>` : ''}</div>
      <div class="subscription-login-identity"><span>Science 登录邮箱</span><strong>${esc(nativeAccount?.user || '未识别')}</strong>${preferredAccount ? (loginDiffers ? '<span class="subscription-label">与优先账号不同</span>' : '<span class="subscription-label">与优先账号一致</span>') : ''}</div>
      <p>${loginDiffers ? '优先消耗决定回复使用哪个账号的额度。Science 的登录邮箱不会自动改变；同步邮箱需要重启一次。' : preferredAccount ? '登录邮箱与优先账号一致。使用备用账号时，可在「最近回复使用」查看。' : '可在账号旁开启优先消耗。更改使用的额度不会自动更改 Science 登录邮箱。'}</p>
      ${loginDiffers ? `<button class="btn" id="subscriptionSyncLogin" ${!connected ? 'disabled' : ''}>${icon('arrow-right-left')}同步 Science 登录为 ${esc(preferredAccount.user)}</button>` : ''}</div>
      ${!connected ? '<button class="btn primary" id="subscriptionHotReconnect">连接免重启模式</button>' : ''}
      <details class="subscription-help"><summary>接入方式</summary><p>首次启用或恢复接入会重启一次；手动切换与自动接管不重启。再次打开 Science 时请从 CSSwitch 打开，以保留路由连接。恢复官方直连也会重启一次。</p><button class="btn" id="subscriptionHotDisable">恢复官方直连</button></details>
    </section></section>`;
  $('#subscriptionLogin').onclick = () => subscriptionAction('login');
  $('#subscriptionUsageRefresh').onclick = () => refreshSubscriptionUsage();
  if ($('#subscriptionHotAuto')) $('#subscriptionHotAuto').onclick = () => subscriptionAction('auto');
  if ($('#subscriptionSyncLogin')) $('#subscriptionSyncLogin').onclick = () => syncScienceLogin(preferred);
  document.querySelectorAll('[data-hot-priority]').forEach(b => { b.onclick = () => subscriptionAction('priority', { id: b.dataset.hotPriority, enabled: b.getAttribute('aria-checked') !== 'true' }); });
  document.querySelectorAll('[data-subscription-login]').forEach(b => { b.onclick = () => subscriptionAction('login'); });
  document.querySelectorAll('[data-subscription-delete]').forEach(b => { b.onclick = () => deleteSubscription(s.accounts.find(a => a.id === b.dataset.subscriptionDelete)); });
  document.querySelectorAll('.subscription-account-menu').forEach(menu => { menu.addEventListener('toggle', () => { if (menu.open) closeSubscriptionMenus(menu); }); menu.addEventListener('click', event => { if (event.target.closest('button:not(:disabled)')) menu.open = false; }); });
  $('#subscriptionHotDisable').onclick = () => subscriptionAction('hot_disable');
  if ($('#subscriptionHotReconnect')) $('#subscriptionHotReconnect').onclick = () => subscriptionAction('hot_enable');
  updateSubscriptionQuotas(); icons();
}

let hotStatusLoading = false;
setInterval(async () => {
  if (!subscriptions?.hot_mode || state.view !== 'subscriptions' || document.hidden || state.busy || hotStatusLoading || document.querySelector('.subscription-account-menu[open]')) return;
  hotStatusLoading = true;
  try {
    const previous = JSON.stringify(subscriptions);
    await loadSubscriptions();
    if (state.view === 'subscriptions' && !state.busy && previous !== JSON.stringify(subscriptions)) renderSubscriptions();
  } catch (_) {} finally { hotStatusLoading = false; }
}, 5000);
