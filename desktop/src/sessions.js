const sessionsState = { rows: [], warnings: [], error: '', search: '', project: 'all', filter: 'active', group: 'time', page: 0, selected: null, messages: [], total: 0, more: false };
const sessionDate = value => { const n = Number(value || 0); return new Date(n < 1e11 ? n * 1000 : n); };
const dateLabel = value => value ? sessionDate(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未知时间';
async function loadSessions() {
  sessionsState.error = ''; sessionsState.warnings = [];
  if (!native()) { sessionsState.error = '请在桌面应用中读取 Science 本地历史。'; sessionsState.rows = []; return; }
  try { const data = await workspaceCall('sessions_list'); sessionsState.rows = data.sessions || []; sessionsState.warnings = data.warnings || []; if (data.limited) sessionsState.warnings.push('每个组织最多显示最近 5000 个主会话。'); }
  catch (e) { sessionsState.error = String(e.message || e); sessionsState.rows = []; }
  if (sessionsState.selected) sessionsState.selected = sessionsState.rows.find(r => r.key === sessionsState.selected.key) || null;
}
function renderSessions() {
  const projects = [...new Map(sessionsState.rows.map(r => [r.org + ':' + (r.project_id || ''), r.project_name || '未分组项目'])).entries()];
  $('#content').innerHTML = `<div class="page-intro"><div><h2>研究历史，随时接着看</h2><p>读取 Science 的本地主会话。收藏、归档和显示名称保存在 CSSwitch 中。</p></div><span class="badge">${icon('hard-drive')} 本地历史 · ${fmt(sessionsState.rows.length)} 个会话</span></div>
    ${sessionsState.error ? `<div class="notice error">${esc(sessionsState.error)}</div>` : ''}${sessionsState.warnings.map(w => `<div class="mcp-note">${esc(w)}</div>`).join('')}
    <div class="management-toolbar"><div class="segmented" aria-label="会话筛选" role="group">${[['active', '全部会话'], ['starred', '收藏'], ['archived', '已归档']].map(([k, label]) => `<button data-session-filter="${k}" class="${sessionsState.filter === k ? 'active' : ''}">${label}</button>`).join('')}</div><select id="sessionGroup" aria-label="会话分组"><option value="time" ${sessionsState.group === 'time' ? 'selected' : ''}>按时间分组</option><option value="project" ${sessionsState.group === 'project' ? 'selected' : ''}>按项目分组</option></select></div>
    <div class="filter-toolbar"><div class="search">${icon('search')}<input id="sessionSearch" type="search" aria-label="搜索会话" placeholder="搜索会话标题、项目或模型" value="${esc(sessionsState.search)}" /></div><select id="sessionProject" aria-label="会话项目"><option value="all">全部项目</option>${projects.map(([id, name]) => `<option value="${esc(id)}" ${sessionsState.project === id ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select></div>
    <div class="sessions-layout"><section><div id="sessionRows"></div><div id="sessionPagination"></div></section><section id="sessionReader" class="session-reader" ${!sessionsState.selected ? 'hidden' : ''}></section></div>`;
  document.querySelectorAll('[data-session-filter]').forEach(b => b.onclick = () => { sessionsState.filter = b.dataset.sessionFilter; sessionsState.page = 0; renderSessions(); });
  $('#sessionSearch').oninput = e => { sessionsState.search = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  $('#sessionProject').onchange = e => { sessionsState.project = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  $('#sessionGroup').onchange = e => { sessionsState.group = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  renderSessionRows(); renderSessionReader(); icons();
}
function renderSessionRows() {
  const q = sessionsState.search.trim().toLowerCase();
  const rows = sessionsState.rows.filter(r => (sessionsState.filter === 'archived' ? r.archived : !r.archived) && (sessionsState.filter !== 'starred' || r.starred) && (sessionsState.project === 'all' || sessionsState.project === r.org + ':' + (r.project_id || '')) && `${r.title} ${r.project_name || ''} ${r.model || ''}`.toLowerCase().includes(q));
  if (sessionsState.group === 'project') rows.sort((a, b) => (a.project_name || '').localeCompare(b.project_name || '', 'zh-CN'));
  const pages = Math.max(1, Math.ceil(rows.length / 25)); sessionsState.page = Math.min(sessionsState.page, pages - 1);
  const visible = rows.slice(sessionsState.page * 25, (sessionsState.page + 1) * 25);
  let group = '';
  $('#sessionRows').innerHTML = visible.map((r, i) => {
    const title = sessionsState.group === 'project' ? r.project_name || '未分组项目' : sessionDate(r.updated_at).toLocaleDateString('zh-CN');
    const heading = title !== group ? `<div class="group-heading">${esc(title)}</div>` : ''; group = title;
    return `${heading}<article class="session-row ${sessionsState.selected?.key === r.key ? 'selected' : ''}" data-session-index="${i}"><button class="session-open" data-session-op="read" aria-label="查看 ${esc(r.title)}"><span class="tile-icon">${icon('message-square')}</span><span class="row-detail"><strong>${esc(r.title)}</strong><span class="session-meta">${esc(r.project_name || '未分组项目')} · ${fmt(r.messages)} 条消息 · ${esc(r.model || '未知模型')}</span><span class="subtle">${dateLabel(r.updated_at)}${r.status ? ` · ${esc({ completed: '已完成', running: '运行中', failed: '失败', pending: '等待中', stopped: '已停止' }[r.status] || r.status)}` : ''}</span></span></button><div class="actions"><button class="icon-btn ${r.starred ? 'starred' : ''}" data-session-op="star" aria-label="${r.starred ? '取消收藏' : '收藏'} ${esc(r.title)}" aria-pressed="${Boolean(r.starred)}">${icon('star')}</button><button class="icon-btn" data-session-op="rename" aria-label="重命名 ${esc(r.title)}">${icon('pencil')}</button><button class="icon-btn" data-session-op="archive" aria-label="${r.archived ? '恢复' : '归档'} ${esc(r.title)}">${icon(r.archived ? 'archive-restore' : 'archive')}</button></div></article>`;
  }).join('') || `<div class="empty">${icon('messages-square')}<strong>${q ? '没有匹配的会话' : '这里还没有会话'}</strong><span>Science 原始历史保持完整，可切换筛选或继续研究后刷新。</span></div>`;
  $('#sessionPagination').innerHTML = `<div class="pagination"><span>${fmt(rows.length)} 个会话 · 第 ${sessionsState.page + 1} / ${pages} 页</span><div class="actions"><button class="btn" id="sessionPrev" ${sessionsState.page === 0 ? 'disabled' : ''}>上一页</button><button class="btn" id="sessionNext" ${sessionsState.page + 1 === pages ? 'disabled' : ''}>下一页</button></div></div>`;
  $('#sessionPrev').onclick = () => { sessionsState.page--; renderSessionRows(); }; $('#sessionNext').onclick = () => { sessionsState.page++; renderSessionRows(); };
  document.querySelectorAll('[data-session-op]').forEach(b => b.onclick = () => action(async () => {
    const r = visible[Number(b.closest('[data-session-index]').dataset.sessionIndex)], op = b.dataset.sessionOp;
    if (op === 'read') {
      const data = await workspaceCall('session_messages', { key: r.key, offset: 0 });
      sessionsState.selected = r; sessionsState.messages = data.messages; sessionsState.total = data.total; sessionsState.more = data.has_more; renderSessions(); return;
    }
    if (op === 'rename') { openSessionRename(r); return; }
    await workspaceCall('session_meta', { key: r.key, changes: op === 'star' ? { starred: !r.starred } : { archived: !r.archived } });
    notice(op === 'star' ? r.starred ? '已取消收藏。' : '已收藏会话。' : r.archived ? '会话已恢复。' : '已在 CSSwitch 中归档，Science 原始历史保留。');
    await loadSessions(); renderSessions();
  })); icons();
}
function renderSessionReader() {
  const r = sessionsState.selected, box = $('#sessionReader'); if (!r || !box) return;
  box.hidden = false;
  box.innerHTML = `<div class="reader-heading"><div><h2>${esc(r.title)}</h2><span class="subtle">${esc(r.project_name || '未分组项目')} · ${fmt(sessionsState.total)} 条消息</span></div><div class="actions"><button class="icon-btn" id="exportSession" aria-label="导出会话 Markdown">${icon('download')}</button><button class="icon-btn" id="closeSession" aria-label="关闭会话阅读">${icon('x')}</button></div></div><div class="transcript">${sessionsState.messages.map(m => `<article class="message ${m.role === 'user' ? 'user' : ''}"><div class="message-role">${icon(m.role === 'user' ? 'user' : 'sparkles')}${esc({ user: '你', assistant: 'Science', system: '系统' }[m.role] || m.role)}<span>#${m.idx + 1}</span></div><div class="message-text">${esc(m.text)}</div>${m.truncated ? '<p class="subtle">消息超过 60,000 字符，已截断。</p>' : ''}</article>`).join('') || '<div class="empty">此会话暂无消息。</div>'}${sessionsState.more ? '<button class="btn" id="moreMessages">加载更多消息</button>' : ''}</div>`;
  $('#closeSession').onclick = () => { sessionsState.selected = null; renderSessions(); };
  $('#moreMessages')?.addEventListener('click', () => action(async () => {
    const data = await workspaceCall('session_messages', { key: r.key, offset: sessionsState.messages.length });
    sessionsState.messages.push(...data.messages); sessionsState.more = data.has_more; renderSessionReader();
  }));
  $('#exportSession').onclick = () => action(async () => {
    const messages = []; let offset = 0, more = true;
    while (more) {
      const data = await workspaceCall('session_messages', { key: r.key, offset });
      if (!data.messages.length) break;
      messages.push(...data.messages); offset += data.messages.length; more = data.has_more;
    }
    const markdown = `# ${r.title}\n\n项目：${r.project_name || '未分组项目'}\n模型：${r.model || '未知'}\n\n` + messages.map(m => `## ${{ user: '你', assistant: 'Science', system: '系统' }[m.role] || m.role}\n\n${m.text}${m.truncated ? '\n\n[消息过长，已截断]' : ''}`).join('\n\n---\n\n');
    download(`csswitch-session-${r.id}.md`, markdown, 'text/markdown'); notice('会话已导出。');
  }); icons();
}
function openSessionRename(r) {
  const dialog = $('#workspaceDialog');
  dialog.innerHTML = `<form id="sessionNameForm"><div class="dialog-heading"><h2 id="workspaceTitle">会话显示名称</h2></div><div class="reader-body"><div class="field"><label for="sessionName">名称</label><input id="sessionName" value="${esc(r.title)}" maxlength="200" required /></div><p class="subtle">只调整 CSSwitch 中的显示名称，Science 原始会话保留。</p><p class="err" id="sessionNameError" role="alert" hidden></p></div><div class="dialog-actions"><button type="button" class="btn" id="cancelSessionName">取消</button><button class="btn primary">保存名称</button></div></form>`;
  $('#cancelSessionName').onclick = () => dialog.close();
  $('#sessionNameForm').onsubmit = e => { e.preventDefault(); action(async () => {
    e.target.inert = true;
    try { await workspaceCall('session_meta', { key: r.key, changes: { title: $('#sessionName').value.trim() } }); await loadSessions(); renderSessions(); dialog.close(); notice('会话显示名称已保存。'); }
    catch (err) { $('#sessionNameError').hidden = false; $('#sessionNameError').textContent = String(err.message || err); }
    finally { e.target.inert = false; }
  }); }; dialog.showModal(); icons();
}
