let subscriptions = null;

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

function renderSubscriptions() {
  if (!subscriptions) return;
  const s = subscriptions;
  const native = s.native || {};
  const nativeMode = !!native.enabled;
  const current = s.accounts.find(a => a.id === native.account_id);
  const running = !!s.science.running;
  const issue = native.last_switch?.phase && !['complete', 'rolled_back'].includes(native.last_switch.phase);
  const planName = value => ({ pro: 'Pro', max: 'Max', team: 'Team', enterprise: 'Enterprise' }[value] || value || '订阅');
  const accountRow = a => {
    const selected = a.id === native.account_id;
    const label = selected ? (running ? 'Science 已登录' : 'Science 已配置') : a.lapsed ? '需要重新登录' : '可切换';
    return `<article class="subscription-account ${selected ? 'is-current' : ''}">
      <div class="subscription-avatar" aria-hidden="true">${esc((a.user || '?').slice(0, 1).toUpperCase())}</div>
      <div class="subscription-account-info"><div class="subscription-account-name"><strong title="${esc(a.user)}">${esc(a.user)}</strong><span class="subscription-plan">${esc(planName(a.plan))}</span></div>
        <div class="subscription-account-meta"><span class="subscription-account-state ${selected ? 'is-current' : ''} ${a.lapsed ? 'is-error' : ''}">${icon(a.lapsed ? 'circle-alert' : selected ? 'circle-check' : 'circle')}${label}</span></div>
      </div>
      <button class="btn subscription-account-action" ${a.lapsed ? 'data-subscription-login' : `data-subscription-id="${esc(a.id)}"`} ${selected && running || issue ? 'disabled' : ''}>${icon(selected && running ? 'check' : a.lapsed ? 'log-in' : 'arrow-right-left')}${a.lapsed ? '重新登录' : selected && running ? '已登录' : '切换登录'}</button>
    </article>`;
  };
  $('#pageTitle').innerHTML = `${icon('users-round')}订阅账号`;
  $('#content').innerHTML = `<section class="subscription-panel" aria-label="订阅账号管理">
    <section class="subscription-card" aria-labelledby="subscriptionClaudeTitle">
      <header class="subscription-card-header"><div class="subscription-provider"><span class="subscription-provider-mark" aria-hidden="true">${icon('sparkles')}</span><div><div class="subscription-title-line"><h2 id="subscriptionClaudeTitle">Claude</h2><span class="subscription-label">官方订阅</span></div><p>切换 Science 真实登录，保留本地项目与会话。</p></div></div><button class="btn primary" id="subscriptionLogin">${icon('plus')}添加账号</button></header>
      ${s.login && s.login.status !== 'success' ? `<div class="subscription-login-status ${s.login.status === 'error' ? 'is-error' : ''}" role="status">${icon(s.login.status === 'error' ? 'circle-alert' : 'clock-3')}<div><strong>${s.login.status === 'error' ? '上次登录未完成' : '等待官方授权'}</strong><p>${esc(s.login.message)}</p></div></div>` : ''}
      <div class="subscription-list-label"><span>账号 <span class="subscription-count">${s.accounts.length}</span></span><span>${s.ready_accounts} 个可用</span></div>
      <div class="subscription-accounts">${s.accounts.map(accountRow).join('') || `<div class="subscription-empty"><span class="subscription-empty-icon">${icon('user-round-plus')}</span><strong>尚未登录 Claude 订阅账号</strong><p>添加你的 Pro、Max 或 Team 账号，即可在这里管理。</p></div>`}</div>
      <footer class="subscription-policy"><div class="subscription-policy-info"><span class="subscription-policy-icon">${icon('arrow-right-left')}</span><div><div class="subscription-policy-title"><strong>切换方式</strong><span class="subscription-mode">原生登录</span></div><p>切换会短暂重启 Science。当前对话会中断，完成后在原会话点击 Resume。</p></div></div></footer>
    </section>
    <section class="subscription-card subscription-science" aria-labelledby="subscriptionScienceTitle">
      <div class="subscription-science-main"><span class="subscription-science-mark" aria-hidden="true">${icon('flask-conical')}</span><div class="subscription-science-info"><div class="subscription-title-line"><h2 id="subscriptionScienceTitle">Claude Science</h2><span class="subscription-connection ${running ? 'is-connected' : ''}"><span class="dot ${running ? 'green' : 'amber'}"></span>${running ? '运行中' : s.installed ? '未启动' : '未安装'}</span></div><p>${nativeMode ? '使用所选账号的真实官方登录，模型请求直接访问官方服务。' : '首次切换将从推理网关迁移到 Science 原生登录。'}</p></div></div>
      <div class="subscription-routing" aria-label="Science 实际登录">
        <div><span>${running ? '当前登录' : '已配置账号'}</span><strong>${esc(current?.user || '尚未识别到已添加的账号')}</strong></div>
        <div><span>会话接续</span><strong>${issue ? '切换未完成，需恢复' : native.last_switch?.phase === 'complete' ? '本地会话已保留' : '切换前自动备份'}</strong>${native.last_switch?.phase === 'complete' ? `<time>${esc(new Date(native.last_switch.at * 1000).toLocaleString('zh-CN'))}</time>` : ''}</div>
        <p>${issue ? '备份位于订阅目录的 native/backups 中，原数据已保留。' : '保留原会话 ID、历史消息和工具结果。Resume 由你在 Science 中触发，避免自动重跑计算。'}</p>${issue ? '<button class="btn" id="subscriptionRecover">恢复原账号</button>' : ''}
      </div>
      <div class="subscription-service-bar"><span><span class="dot ${nativeMode ? 'green' : 'amber'}"></span>${nativeMode ? 'Science 管理登录凭据' : '旧推理网关模式'}</span><span>${nativeMode ? '订阅网关不参与推理' : '点击账号的「切换登录」完成迁移'}</span></div>
    </section>
    <details class="subscription-help"><summary>${icon('circle-help')}切换与 Resume 说明${icon('chevron-down')}</summary><div><p>切换先停止 Science，再备份登录和完整本地会话目录；启动后核对 Science 自身返回的账号身份。目标账号已有独立更新的历史时会停止迁移，避免覆盖。</p><p>支持本机 Science 0.1.72。Resume 保留对话上下文；内存中的 Python/R 对象、仍运行的本地进程、远程任务或账号专属云端资源能否恢复，取决于 Science 自身能力。</p></div></details>
  </section>`;
  $('#subscriptionLogin').onclick = () => subscriptionAction('login');
  if ($('#subscriptionRecover')) $('#subscriptionRecover').onclick = () => subscriptionAction('recover');
  document.querySelectorAll('[data-subscription-id]').forEach(b => {
    b.onclick = () => subscriptionAction('switch', { id: b.dataset.subscriptionId });
  });
  document.querySelectorAll('[data-subscription-login]').forEach(b => {
    b.onclick = () => subscriptionAction('login');
  });
  icons();
}
