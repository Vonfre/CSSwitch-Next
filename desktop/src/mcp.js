// MCP management uses the native Science client; credentials stay in the bridge.
const mcpState = { data: null, error: '', tools: null, server: null };
async function mcpCall(operation, params = {}) { return call('mcp_manage', { operation, params }); }
async function loadMcp() {
  mcpState.data = null; mcpState.error = ''; mcpState.tools = null;
  if (!native()) { mcpState.error = '请在桌面应用中管理 MCP。'; return; }
  try { mcpState.data = await mcpCall('list'); }
  catch (e) { mcpState.error = String(e.message || e); }
}
const mcpSources = { 'local-stdio': '本地 stdio', custom: '远程 MCP', bundled: 'Science 内置', directory: 'Claude 目录', antmcp: '托管 MCP' };
const mcpStatuses = { connected: '已连接', connecting: '连接中', pending: '加载中', failed: '连接失败', error: '连接失败', auth_required: '需要授权', disabled: '已停用', loaded: '工具已加载' };
function renderMcp() {
  if (!mcpState.data) {
    $('#content').innerHTML = `<div class="empty">${icon('network')}<strong>MCP 管理暂不可用</strong><span>${esc(mcpState.error || '正在读取连接器…')}</span><button class="btn" id="mcpWorkbench">返回提供商</button></div>`;
    $('#mcpWorkbench').onclick = () => showView('workbench'); icons(); return;
  }
  const data = mcpState.data;
  const rows = data.connectors || [];
  const installed = rows.some(c => c.name === 'csswitch-openscience');
  const directoryUnavailable = data.directoryHealth && !data.directoryHealth.ok;
  $('#content').innerHTML = `<section class="mcp-intro"><div><h2>科研数据库与自定义 MCP</h2><p>安装 OpenScience 数据库服务后，可在 Science 对话中检索 PubMed、UniProt、ChEMBL 等数据源。需要 Node.js 22 或以上，部分数据库需要单独的 API Key。</p></div><button class="btn primary" id="installScienceMcp" ${!data.customAllowed || installed ? 'disabled' : ''}>${icon('download')}${installed ? '科研服务已安装' : '安装科研 MCP'}</button></section>
    ${directoryUnavailable ? `<div class="mcp-note">Claude 官方目录当前不可用${data.directoryHealth.status ? `（HTTP ${esc(data.directoryHealth.status)}）` : ''}。下面的本地和自定义 MCP 使用各自的连接与授权，不依赖 Claude 目录登录。</div>` : ''}
    ${!data.customAllowed ? '<div class="notice error">Science 组织策略禁止自定义连接器。</div>' : ''}
    <div class="section-heading"><h2>连接器 <span class="label-count">${rows.length} 个</span></h2><button class="btn" id="addMcp" ${!data.customAllowed ? 'disabled' : ''}>${icon('plus')}添加 MCP</button></div>
    <div class="mcp-list">${rows.map(c => {
      const manageable = ['custom', 'local-stdio', 'antmcp'].includes(c.source);
      const bound = (c.attachedAgents || []).includes('OPERON');
      const blocked = c.admin_blocked || (manageable && !data.customAllowed);
      const status = c.admin_blocked ? '策略禁用' : c.enabled === false ? '已停用' : mcpStatuses[c.connectionStatus] || (c.authState === 'unauthorized' ? '需要授权' : bound ? '已绑定 Science' : '未绑定 Science');
      return `<article class="mcp-row" data-mcp-id="${esc(c.id)}"><div class="mcp-meta"><h3>${esc(c.displayName || c.name)} <span class="badge">${esc(mcpSources[c.source] || c.source)}</span></h3><p>${esc(c.description || c.name)}</p><span class="subtle">${esc(status)} · ${esc(c.transport)}${c.attachedAgents?.length ? ` · 绑定：${esc(c.attachedAgents.join(', '))}` : ''}</span></div><div class="actions">
        <button class="btn" data-mcp-op="tools">工具</button>
        ${manageable ? `<button class="btn" data-mcp-op="${bound ? 'detach' : 'attach'}" ${blocked ? 'disabled' : ''}>${bound ? '解绑 Science' : '绑定 Science'}</button>` : ''}
        ${c.source === 'bundled' ? `<button class="btn" data-mcp-op="enabled" ${blocked ? 'disabled' : ''}>${c.enabled === false ? '启用' : '停用'}</button>` : ''}
        ${['custom', 'antmcp'].includes(c.source) && c.authState !== 'not-required' ? `<button class="btn" data-mcp-op="authorize" ${blocked ? 'disabled' : ''}>授权</button>` : ''}
        ${['custom', 'local-stdio'].includes(c.source) ? `<button class="icon-btn danger" data-mcp-op="remove" title="删除 ${esc(c.name)}" aria-label="删除 ${esc(c.name)}">${icon('trash-2')}</button>` : ''}</div></article>`;
    }).join('') || '<div class="empty">暂无连接器。安装科研 MCP 或添加自己的服务。</div>'}</div><section id="mcpTools"></section>`;
  $('#addMcp').onclick = () => openMcpEditor();
  $('#installScienceMcp').onclick = () => openMcpEditor(true);
  document.querySelectorAll('[data-mcp-op]').forEach(button => { button.onclick = () => action(async () => {
    const c = rows.find(c => c.id === button.closest('[data-mcp-id]').dataset.mcpId);
    const op = button.dataset.mcpOp;
    if (op === 'tools') {
      mcpState.server = c; mcpState.tools = await mcpCall('tools', { id: c.id }); renderMcpTools(); return;
    }
    if (op === 'remove' && !await confirmAction(`删除 ${c.name}？`, '将移除连接器及其保存的授权，并解除所有代理绑定。')) return;
    const result = await mcpCall(op, { id: c.id, ...(op === 'enabled' ? { enabled: c.enabled === false } : {}) });
    notice(result.message || '连接器已更新。'); await loadMcp(); renderMcp();
  }); });
  icons();
}
function renderMcpTools() {
  const data = mcpState.tools, c = mcpState.server;
  const box = $('#mcpTools');
  if (!box || !data) return;
  box.innerHTML = `<div class="section-heading"><h2>${esc(c.name)} 的工具</h2><span class="subtle">${esc(mcpStatuses[data.status] || data.status)}</span></div>
    ${data.skipApprovalsActive ? '<p class="mcp-note">该服务已在 Science 中设置跳过审批；单项权限可能受此设置影响。</p>' : ''}
    ${data.tools.length ? data.tools.map(t => `<div class="mcp-tool"><div><strong>${esc(t.toolName)}</strong>${t.readOnlyHint ? '<span class="badge">只读声明</span>' : ''}<p>${esc(t.description)}</p></div><select data-tool-name="${esc(t.toolName)}" aria-label="${esc(t.toolName)} 权限"><option value="" ${t.state === 'unset' ? 'selected' : ''}>默认审批</option><option value="ask" ${t.state === 'ask' ? 'selected' : ''}>每次询问</option><option value="allow" ${t.state === 'allow' ? 'selected' : ''}>总是允许</option><option value="deny" ${t.state === 'deny' ? 'selected' : ''}>禁止</option></select></div>`).join('') : '<p class="subtle">工具尚未加载。请检查绑定与授权，然后重新点击工具。</p>'}`;
  box.querySelectorAll('[data-tool-name]').forEach(select => { select.onchange = () => action(async () => {
    try {
      await mcpCall('grant', { id: c.id, toolName: select.dataset.toolName, decision: select.value || null }); notice('工具权限已更新。');
    } finally { mcpState.tools = await mcpCall('tools', { id: c.id }); renderMcpTools(); }
  }); });
  icons();
}
function openMcpEditor(install = false) {
  const dialog = $('#mcpDialog');
  dialog.innerHTML = `<form id="mcpForm"><div class="dialog-heading"><h2 id="mcpTitle">${install ? '安装 OpenScience 科研 MCP' : '添加 MCP'}</h2><button type="button" class="icon-btn" id="closeMcp" aria-label="关闭 MCP 表单">${icon('x')}</button></div><div class="editor-body">
    <div class="form-grid">${!install ? `<div class="field"><label for="mcpName">名称</label><input id="mcpName" name="name" pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" maxlength="64" placeholder="my-mcp" required /></div><div class="field"><label for="mcpType">连接方式</label><select id="mcpType"><option value="local">本地 stdio</option><option value="remote">远程 HTTPS</option></select></div><div class="field wide"><label for="mcpDescription">描述</label><input id="mcpDescription" name="description" maxlength="1000" /></div>
      <div id="mcpLocal" class="field wide"><div class="field"><label for="mcpCommand">可执行文件绝对路径</label><input id="mcpCommand" name="command" placeholder="/opt/homebrew/bin/uv" required /></div><div class="field"><label for="mcpArgs">参数（JSON 字符串数组）</label><textarea id="mcpArgs" rows="2" spellcheck="false">[]</textarea></div></div>
      <div id="mcpRemote" class="field wide" hidden><div class="field"><label for="mcpUrl">公共 HTTPS MCP 地址</label><input id="mcpUrl" name="url" type="url" placeholder="https://example.com/mcp" /></div><div class="field"><label for="mcpTransport">传输协议</label><select id="mcpTransport" name="transport"><option value="streamable_http">Streamable HTTP</option><option value="sse">SSE</option></select></div><details><summary>OAuth 与请求头配置</summary><div class="field"><label for="mcpOauthServer">OAuth 服务地址（可选）</label><input id="mcpOauthServer" name="oauth_server_url" type="url" /></div><div class="field"><label for="mcpClient">客户端 ID（可选）</label><input id="mcpClient" name="client_id" /></div><div class="field"><label for="mcpScopes">Scopes（可选）</label><input id="mcpScopes" name="scopes" /></div><div class="field"><label for="mcpHeaders">Science headers_helper 命令（可选）</label><input id="mcpHeaders" name="headers_helper" placeholder="/absolute/path/to/headers-helper" /><p class="subtle">命令须输出请求头 JSON，由 Science 执行。授权信息使用服务商自己的账号或密钥。</p></div></details></div>` : '<p class="subtle field wide">将安装本地只读数据库服务并绑定 Science。工具调用保留默认审批。数据库清单可在对话中通过 science_list_dbs 查看。</p>'}
    <div id="mcpEnvField" class="field wide"><label for="mcpEnv">环境变量（JSON 字符串对象，可选）</label><textarea id="mcpEnv" rows="3" autocomplete="off" spellcheck="false" placeholder='{"BIOGRID_ACCESS_KEY":"..."}'>{}</textarea><span class="subtle">仅用于本地 MCP。密钥交给 Science 保存，不会在连接器列表回显。</span></div></div>
    <p id="mcpFormError" class="err" role="alert" hidden></p>${!install ? '<label><input type="checkbox" id="mcpAttach" checked /> 添加后绑定 Science（OPERON）</label>' : ''}</div><div class="dialog-actions"><button type="button" class="btn" id="cancelMcp">取消</button><button class="btn primary" type="submit">${install ? '安装并绑定' : '添加'}</button></div></form>`;
  const close = () => { if (!state.busy) { dialog.close(); dialog.innerHTML = ''; } };
  $('#closeMcp').onclick = close; $('#cancelMcp').onclick = close;
  dialog.oncancel = e => { if (state.busy) e.preventDefault(); };
  dialog.onclose = () => { dialog.innerHTML = ''; };
  if (!install) $('#mcpType').onchange = () => {
    const remote = $('#mcpType').value === 'remote';
    $('#mcpLocal').hidden = remote; $('#mcpRemote').hidden = !remote; $('#mcpEnvField').hidden = remote;
    $('#mcpCommand').required = !remote; $('#mcpUrl').required = remote;
  };
  $('#mcpForm').onsubmit = e => { e.preventDefault(); action(async () => {
    const form = e.target;
    const error = $('#mcpFormError'); error.hidden = true;
    form.inert = true;
    try {
      const remote = !install && $('#mcpType').value === 'remote';
      const params = install ? { env: JSON.parse($('#mcpEnv').value || '{}') } : {
        ...Object.fromEntries(new FormData(form)), attach: $('#mcpAttach').checked,
        ...(remote ? {} : { args: JSON.parse($('#mcpArgs').value), env: JSON.parse($('#mcpEnv').value || '{}') }),
      };
      const value = await mcpCall(install ? 'install' : remote ? 'add_remote' : 'add_local', params);
      dialog.close(); notice(value.message || 'MCP 已添加。'); await loadMcp(); renderMcp();
    } catch (e) { error.textContent = e instanceof SyntaxError ? '参数或环境变量的 JSON 格式无效。' : String(e.message || e); error.hidden = false; }
    finally { form.inert = false; }
  }); };
  dialog.showModal(); icons();
}
