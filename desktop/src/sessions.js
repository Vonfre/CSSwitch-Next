const sessionsState = { rows: [], warnings: [], error: '', search: '', project: 'all', filter: 'active', group: 'time', page: 0, selected: null, messages: [], total: 0, more: false, closed: false, readerFilter: 'all', readerSearch: '', outline: false };
const sessionDate = value => { const n = Number(value || 0); return new Date(n < 1e11 ? n * 1000 : n); };
const dateLabel = value => value ? sessionDate(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '未知时间';
function filteredSessions() {
  const q = sessionsState.search.trim().toLowerCase();
  const rows = sessionsState.rows.filter(r => (sessionsState.filter === 'archived' ? r.archived : !r.archived) && (sessionsState.filter !== 'starred' || r.starred) && (sessionsState.project === 'all' || sessionsState.project === r.org + ':' + (r.project_id || '')) && `${r.title} ${r.preview || ''} ${r.project_name || ''} ${r.model || ''}`.toLowerCase().includes(q));
  if (sessionsState.group === 'project') rows.sort((a, b) => (a.project_name || '').localeCompare(b.project_name || '', 'zh-CN'));
  return rows;
}
async function readSession(r) {
  const data = await workspaceCall('session_messages', { key: r.key, offset: 0 });
  sessionsState.selected = r; sessionsState.messages = data.messages || []; sessionsState.total = data.total; sessionsState.more = data.has_more;
  sessionsState.closed = false; sessionsState.readerSearch = ''; sessionsState.readerFilter = 'all';
}
async function loadSessions(refreshReader = false) {
  sessionsState.error = ''; sessionsState.warnings = [];
  if (!native()) { sessionsState.error = '请在桌面应用中读取 Science 本地历史。'; sessionsState.rows = []; return; }
  try { const data = await workspaceCall('sessions_list'); sessionsState.rows = data.sessions || []; sessionsState.warnings = data.warnings || []; if (data.limited) sessionsState.warnings.push('每个组织最多显示最近 5000 个主会话。'); }
  catch (e) { sessionsState.error = String(e.message || e); sessionsState.rows = []; }
  if (sessionsState.selected) sessionsState.selected = sessionsState.rows.find(r => r.key === sessionsState.selected.key) || null;
  if (sessionsState.selected && refreshReader) {
    try {
      const data = await workspaceCall('session_messages', { key:sessionsState.selected.key, offset:0 });
      sessionsState.messages = data.messages || []; sessionsState.total = data.total; sessionsState.more = data.has_more;
    } catch(e) { sessionsState.warnings.push('刷新会话内容失败：' + String(e.message || e)); }
  }
  if (!sessionsState.selected && !sessionsState.closed && filteredSessions().length) {
    try { await readSession(filteredSessions()[0]); }
    catch(e) { sessionsState.warnings.push('会话内容暂不可读：' + String(e.message || e)); }
  }
}
function renderSessions() {
  const projects = [...new Map(sessionsState.rows.map(r => [r.org + ':' + (r.project_id || ''), r.project_name || '未分组项目'])).entries()];
  $('#content').innerHTML = `<div class="page-intro compact-intro session-intro"><div><h2>每一次研究，都有迹可循</h2><p>浏览本地会话和完整记录，随时找回研究思路。</p></div><span class="badge">${icon('hard-drive')} ${fmt(sessionsState.rows.length)} 个本地会话</span></div>
    ${sessionsState.error ? `<div class="notice error">${esc(sessionsState.error)}</div>` : ''}${sessionsState.warnings.map(w => `<div class="mcp-note">${esc(w)}</div>`).join('')}
    <div class="sessions-layout ${sessionsState.selected ? 'has-reader' : ''}"><section class="session-sidebar"><div class="session-list-toolbar"><div class="segmented" aria-label="会话筛选" role="group">${[['active', '全部会话'], ['starred', '收藏'], ['archived', '已归档']].map(([k,label]) => `<button data-session-filter="${k}" class="${sessionsState.filter === k ? 'active' : ''}">${label}</button>`).join('')}</div><div class="search">${icon('search')}<input id="sessionSearch" type="search" aria-label="搜索会话" placeholder="搜索会话或消息摘要" value="${esc(sessionsState.search)}" /></div><div class="session-selects"><select id="sessionProject" aria-label="会话项目"><option value="all">全部项目</option>${projects.map(([id,name]) => `<option value="${esc(id)}" ${sessionsState.project === id ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select><select id="sessionGroup" aria-label="会话分组"><option value="time" ${sessionsState.group === 'time' ? 'selected' : ''}>按时间</option><option value="project" ${sessionsState.group === 'project' ? 'selected' : ''}>按项目</option></select></div></div><div id="sessionRows" class="session-list-scroll"></div><div id="sessionPagination"></div></section><section id="sessionReader" class="session-reader" aria-label="会话内容"></section></div>`;
  document.querySelectorAll('[data-session-filter]').forEach(b => b.onclick = () => { sessionsState.filter = b.dataset.sessionFilter; sessionsState.page = 0; renderSessions(); });
  $('#sessionSearch').oninput = e => { sessionsState.search = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  $('#sessionProject').onchange = e => { sessionsState.project = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  $('#sessionGroup').onchange = e => { sessionsState.group = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  renderSessionRows(); renderSessionReader(); icons();
}
function sessionGroupLabel(r) {
  if (sessionsState.group === 'project') return r.project_name || '未分组项目';
  const day = sessionDate(r.updated_at), today = new Date();
  if (day.toDateString() === today.toDateString()) return '今天';
  today.setDate(today.getDate() - 1); if (day.toDateString() === today.toDateString()) return '昨天';
  return day.toLocaleDateString('zh-CN', {month:'long',day:'numeric'});
}
function renderSessionRows() {
  const rows = filteredSessions(), pages = Math.max(1, Math.ceil(rows.length / 25)); sessionsState.page = Math.min(sessionsState.page, pages - 1);
  const visible = rows.slice(sessionsState.page * 25, (sessionsState.page + 1) * 25); let group = '';
  $('#sessionRows').innerHTML = visible.map((r,i) => {
    const title = sessionGroupLabel(r), heading = title !== group ? `<div class="group-heading">${esc(title)}</div>` : ''; group = title;
    return `${heading}<article class="session-row ${sessionsState.selected?.key === r.key ? 'selected' : ''}" data-session-index="${i}"><button class="session-open" data-session-op="read" aria-label="查看 ${esc(r.title)}" aria-pressed="${sessionsState.selected?.key === r.key}"><span class="row-detail"><strong>${esc(r.title)}</strong>${r.preview ? `<span class="session-preview">${esc(r.preview)}</span>` : ''}<span class="session-meta">${esc(r.project_name || '未分组项目')} · ${fmt(r.messages)} 条消息</span><span class="session-time">${dateLabel(r.updated_at)}${r.model ? ` · ${esc(r.model)}` : ''}</span></span></button><div class="actions session-row-actions"><button class="icon-btn ${r.starred ? 'starred' : ''}" data-session-op="star" aria-label="${r.starred ? '取消收藏' : '收藏'} ${esc(r.title)}" aria-pressed="${Boolean(r.starred)}" title="收藏">${icon('star')}</button><button class="icon-btn" data-session-op="rename" aria-label="重命名 ${esc(r.title)}" title="重命名">${icon('pencil')}</button><button class="icon-btn" data-session-op="archive" aria-label="${r.archived ? '恢复' : '归档'} ${esc(r.title)}" title="${r.archived ? '恢复' : '归档'}">${icon(r.archived ? 'archive-restore' : 'archive')}</button></div></article>`;
  }).join('') || `<div class="empty">${icon('messages-square')}<strong>没有匹配的会话</strong><span>切换筛选，或继续研究后刷新。</span></div>`;
  $('#sessionPagination').innerHTML = `<div class="pagination"><span>${fmt(rows.length)} 个 · ${sessionsState.page + 1}/${pages}</span><div class="actions"><button class="icon-btn" id="sessionPrev" aria-label="上一页" ${sessionsState.page === 0 ? 'disabled' : ''}>${icon('chevron-left')}</button><button class="icon-btn" id="sessionNext" aria-label="下一页" ${sessionsState.page + 1 === pages ? 'disabled' : ''}>${icon('chevron-right')}</button></div></div>`;
  $('#sessionPrev').onclick = () => { sessionsState.page--; renderSessionRows(); }; $('#sessionNext').onclick = () => { sessionsState.page++; renderSessionRows(); };
  document.querySelectorAll('[data-session-op]').forEach(b => b.onclick = () => action(async () => {
    const r = visible[Number(b.closest('[data-session-index]').dataset.sessionIndex)], op = b.dataset.sessionOp;
    if (op === 'read') { await readSession(r); renderSessions(); return; }
    if (op === 'rename') { openSessionRename(r); return; }
    await workspaceCall('session_meta', { key:r.key, changes:op === 'star' ? { starred:!r.starred } : { archived:!r.archived } });
    if (op === 'archive' && sessionsState.selected?.key === r.key) { sessionsState.selected = null; sessionsState.closed = false; }
    notice(op === 'star' ? r.starred ? '已取消收藏。' : '已收藏会话。' : r.archived ? '会话已恢复。' : '已归档，Science 原始历史保留。');
    await loadSessions(); renderSessions();
  })); icons();
}
function transcriptBlocks(m) { return m.blocks?.length ? m.blocks : [{ type:'text', text:m.text || '' }]; }
function renderMessageBlock(block) {
  if (block.type === 'text') return `<div class="message-text markdown">${markdown(block.text)}</div>`;
  if (block.type === 'attachment') return `<div class="attachment-note">${icon('paperclip')}${esc(block.text)}</div>`;
  const labels = { tool_use:'工具调用', tool_result:'工具结果', thinking:'思考过程' };
  return `<details class="message-step ${block.type}"><summary>${icon(block.type === 'thinking' ? 'brain' : 'terminal')}<strong>${esc(block.name || labels[block.type] || block.type)}</strong><span>${esc(labels[block.type] || '')}</span>${icon('chevron-down')}</summary><pre><code>${esc(block.text)}</code></pre>${block.truncated ? '<span class="subtle">内容过长，已截断。</span>' : ''}</details>`;
}
function renderSessionReader() {
  const r = sessionsState.selected, box = $('#sessionReader'); if (!box) return;
  if (!r) { box.innerHTML = `<div class="reader-empty"><div class="empty-illustration">${icon('messages-square')}</div><h2>打开一段研究记录</h2><p>从左侧选择会话，阅读提问、回复和工具记录。</p><span class="subtle">${icon('hard-drive')}本地读取 · 原始历史保留</span></div>`; icons(); return; }
  box.innerHTML = `<div class="reader-heading"><button class="icon-btn reader-back" id="closeSession" aria-label="关闭会话阅读" title="返回会话列表">${icon('arrow-left')}</button><div><h2>${esc(r.title)}</h2><span class="subtle">${icon('folder-open')}${esc(r.project_name || '未分组项目')} · ${dateLabel(r.updated_at)}</span></div><div class="actions"><button class="icon-btn ${r.starred ? 'starred' : ''}" id="readerStar" aria-label="收藏当前会话" aria-pressed="${Boolean(r.starred)}" title="收藏">${icon('star')}</button><button class="icon-btn" id="exportSession" aria-label="导出会话 Markdown" title="导出 Markdown">${icon('download')}</button></div></div><div class="reader-info"><span class="model-pill">${icon('sparkles')}${esc(r.model || 'Science')}</span><span>${fmt(sessionsState.total)} 条消息</span>${r.input_tokens || r.output_tokens ? `<span>${fmt((r.input_tokens || 0) + (r.output_tokens || 0))} Tokens</span>` : ''}<span class="status-badge">${esc({completed:'已完成',running:'运行中',failed:'失败',pending:'等待中',stopped:'已停止'}[r.status] || r.status || '本地记录')}</span></div><div class="reader-controls"><div class="segmented" role="group" aria-label="记录类型">${[['all','全部'],['dialogue','对话'],['tools','工具']].map(([key,label]) => `<button data-transcript-filter="${key}" class="${sessionsState.readerFilter === key ? 'active' : ''}">${label}</button>`).join('')}</div><div class="actions"><button class="icon-btn ${sessionsState.outline ? 'active' : ''}" id="toggleOutline" aria-label="切换对话目录" aria-pressed="${sessionsState.outline}" title="对话目录">${icon('panel-right')}</button><button class="icon-btn" id="latestMessage" aria-label="跳到最新消息" title="跳到最新消息">${icon('arrow-down-to-line')}</button></div></div><div class="reader-search search">${icon('search')}<input id="transcriptSearch" type="search" aria-label="搜索会话内容" placeholder="搜索已加载的消息内容" value="${esc(sessionsState.readerSearch)}" /></div><div class="reader-content ${sessionsState.outline ? 'with-outline' : ''}"><div class="transcript" tabindex="0" aria-label="消息记录"></div><aside id="sessionOutline" class="session-outline" ${sessionsState.outline ? '' : 'hidden'}></aside></div>`;
  $('#closeSession').onclick = () => { sessionsState.selected = null; sessionsState.closed = true; renderSessions(); };
  $('#readerStar').onclick = () => action(async () => { await workspaceCall('session_meta', {key:r.key,changes:{starred:!r.starred}}); await loadSessions(); renderSessions(); });
  $('#exportSession').onclick = () => action(() => exportSession(r));
  $('#transcriptSearch').oninput = e => { sessionsState.readerSearch = e.target.value; renderTranscript(); };
  document.querySelectorAll('[data-transcript-filter]').forEach(b => b.onclick = () => { sessionsState.readerFilter = b.dataset.transcriptFilter; document.querySelectorAll('[data-transcript-filter]').forEach(v => v.classList.toggle('active',v === b)); renderTranscript(); });
  $('#toggleOutline').onclick = () => { sessionsState.outline = !sessionsState.outline; $('.reader-content').classList.toggle('with-outline',sessionsState.outline); $('#sessionOutline').hidden = !sessionsState.outline; $('#toggleOutline').setAttribute('aria-pressed',String(sessionsState.outline)); };
  $('#latestMessage').onclick = () => { const transcript = $('.transcript'); transcript.scrollTop = transcript.scrollHeight; };
  renderTranscript(); icons();
}
function renderTranscript() {
  const box = $('.transcript'); if (!box) return;
  const q = sessionsState.readerSearch.trim().toLowerCase(), filter = sessionsState.readerFilter;
  const visible = sessionsState.messages.map(m => ({...m, blocks:transcriptBlocks(m).filter(b => filter === 'all' || filter === 'dialogue' && b.type === 'text' || filter === 'tools' && ['tool_use','tool_result'].includes(b.type))})).filter(m => m.blocks.length && (!q || m.blocks.some(b => `${b.text} ${b.name || ''}`.toLowerCase().includes(q))));
  box.innerHTML = visible.map(m => `<article class="message ${m.role === 'user' ? 'user' : 'assistant'}" id="message-${Number(m.idx)}"><div class="message-role"><span class="message-avatar">${icon(m.role === 'user' ? 'user' : 'sparkles')}</span><strong>${esc({user:'你',assistant:'Science',system:'系统'}[m.role] || m.role)}</strong><span>#${Number(m.idx) + 1}</span></div><div class="message-body">${m.blocks.map(renderMessageBlock).join('')}${m.truncated ? '<p class="subtle">消息超过 60,000 字符，已截断。</p>' : ''}</div></article>`).join('') || '<div class="empty">没有匹配的消息。</div>';
  if (sessionsState.more) box.insertAdjacentHTML('beforeend', '<div class="load-more"><p class="subtle">已加载 ' + sessionsState.messages.length + ' / ' + sessionsState.total + ' 条消息</p><button class="btn" id="moreMessages">加载更多消息</button></div>');
  const questions = visible.filter(m => m.role === 'user' && m.blocks.some(b => b.type === 'text'));
  $('#sessionOutline').innerHTML = `<div class="outline-heading">对话目录 <span>${questions.length}</span></div>${questions.map(m => `<button data-message-jump="${Number(m.idx)}"><span>你 · #${Number(m.idx) + 1}</span><strong>${esc(m.blocks.find(b => b.type === 'text')?.text.slice(0,100))}</strong></button>`).join('') || '<p class="subtle">当前筛选没有提问。</p>'}`;
  document.querySelectorAll('[data-message-jump]').forEach(b => b.onclick = () => document.getElementById('message-' + b.dataset.messageJump)?.scrollIntoView({block:'start',behavior:'smooth'}));
  $('#moreMessages')?.addEventListener('click', () => action(async () => {
    const y = box.scrollTop, r = sessionsState.selected;
    const data = await workspaceCall('session_messages', { key:r.key, offset:sessionsState.messages.length });
    sessionsState.messages.push(...(data.messages || [])); sessionsState.more = data.has_more; sessionsState.total = data.total; renderTranscript(); $('.transcript').scrollTop = y;
  })); bindMarkdown(box); icons();
}
async function exportSession(r) {
  const messages = []; let offset = 0, more = true;
  while (more) {
    const data = await workspaceCall('session_messages', { key:r.key, offset }); if (!data.messages.length) break;
    messages.push(...data.messages); offset += data.messages.length; more = data.has_more;
  }
  const text = `# ${r.title}\n\n项目：${r.project_name || '未分组项目'}\n模型：${r.model || '未知'}\n\n` + messages.map(m => `## ${{user:'你',assistant:'Science',system:'系统'}[m.role] || m.role}\n\n${m.text}${m.truncated ? '\n\n[消息过长，已截断]' : ''}`).join('\n\n---\n\n');
  download(`csswitch-session-${r.id}.md`, text, 'text/markdown'); notice('完整会话已导出。');
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
