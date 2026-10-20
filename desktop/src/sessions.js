const sessionsState = { rows: [], warnings: [], error: '', search: '', project: 'all', filter: 'active', group: 'project', page: 0, selected: null, messages: [], total: 0, more: false, readerFilter: 'all', readerSearch: '', searchOpen: false, outline: false, collapsedGroups: new Set(), groupsInitialized: false, expandedSteps: new Set(), expandedTimelines: new Set() };
const sessionDate = value => { const n = Number(value || 0); return typeof value === 'string' && !Number.isFinite(n) ? new Date(value) : new Date(n < 1e11 ? n * 1000 : n); };
const dateLabel = value => value ? sessionDate(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '';
const shortSessionDate = value => value ? sessionDate(value).toLocaleDateString('zh-CN', {month:'long',day:'numeric'}) : '未知时间';
function filteredSessions() {
  const q = sessionsState.search.trim().toLowerCase();
  return sessionsState.rows.filter(r => (sessionsState.filter === 'archived' ? r.archived : !r.archived) && (sessionsState.filter !== 'starred' || r.starred) && (sessionsState.project === 'all' || sessionsState.project === r.org + ':' + (r.project_id || '')) && `${r.title} ${r.preview || ''} ${r.last_preview || ''} ${r.project_name || ''} ${r.model || ''} ${r.id}`.toLowerCase().includes(q));
}
async function readSession(r) {
  const data = await workspaceCall('session_messages', { key: r.key, offset: 0 });
  sessionsState.selected = r; sessionsState.messages = data.messages || []; sessionsState.total = data.total; sessionsState.more = data.has_more;
  sessionsState.readerSearch = ''; sessionsState.readerFilter = 'all'; sessionsState.searchOpen = false; sessionsState.outline = false;
  sessionsState.expandedSteps.clear(); sessionsState.expandedTimelines.clear();
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
}
function renderSessions() {
  document.body.classList.toggle('session-detail', Boolean(sessionsState.selected));
  $('#pageTitle').innerHTML = `${icon('history')}<span>会话</span>`;
  const warnings = `${sessionsState.error ? `<div class="notice error">${esc(sessionsState.error)}</div>` : ''}${sessionsState.warnings.map(w => `<div class="mcp-note">${esc(w)}</div>`).join('')}`;
  if (sessionsState.selected) {
    $('#content').innerHTML = `${warnings}<section id="sessionReader" class="session-reader" aria-label="会话内容"></section>`;
    renderSessionReader(); icons(); return;
  }
  const projects = [...new Map(sessionsState.rows.map(r => [r.org + ':' + (r.project_id || ''), r.project_name || '未分组项目'])).entries()];
  $('#content').innerHTML = `${warnings}<section class="session-browser"><div class="session-browser-toolbar"><div class="session-source">${icon('sparkles')}<strong>Science</strong><span>${fmt(sessionsState.rows.length)}</span></div><div class="search session-browser-search">${icon('search')}<input id="sessionSearch" type="search" aria-label="搜索会话" placeholder="搜索标题、项目、首末消息或会话 ID" value="${esc(sessionsState.search)}" /></div><div class="segmented session-group-toggle" role="group" aria-label="会话分组">${[['time','按时间'],['project','按项目']].map(([key,label])=>`<button data-session-group="${key}" class="${sessionsState.group===key?'active':''}">${label}</button>`).join('')}</div></div><div class="session-browser-options"><span>本地会话 ${help('会话记录', '只读浏览 Science 原始历史。收藏、归档和重命名保存在 CSSwitch 中，不修改原始会话；支持导出完整 Markdown。')}</span><div class="session-browser-filters"><select id="sessionProject" aria-label="会话项目"><option value="all">全部项目</option>${projects.map(([id,name]) => `<option value="${esc(id)}" ${sessionsState.project === id ? 'selected' : ''}>${esc(name)}</option>`).join('')}</select><div class="segmented" aria-label="会话筛选" role="group">${[['active','全部会话'],['starred','收藏'],['archived','已归档']].map(([k,label])=>`<button data-session-filter="${k}" class="${sessionsState.filter===k?'active':''}">${label}</button>`).join('')}</div></div></div><div id="sessionRows" class="session-groups"></div><div id="sessionPagination"></div></section>`;
  document.querySelectorAll('[data-session-filter]').forEach(b => b.onclick = () => { sessionsState.filter = b.dataset.sessionFilter; sessionsState.page = 0; renderSessions(); });
  document.querySelectorAll('[data-session-group]').forEach(b => b.onclick = () => { sessionsState.group = b.dataset.sessionGroup; sessionsState.page = 0; renderSessions(); });
  $('#sessionSearch').oninput = e => { sessionsState.search = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  $('#sessionProject').onchange = e => { sessionsState.project = e.target.value; sessionsState.page = 0; renderSessionRows(); };
  renderSessionRows(); icons();
}
function sessionGroupLabel(r) {
  if (sessionsState.group === 'project') return r.project_name || '未分组项目';
  const day = sessionDate(r.updated_at), today = new Date();
  if (day.toDateString() === today.toDateString()) return '今天';
  today.setDate(today.getDate() - 1); if (day.toDateString() === today.toDateString()) return '昨天';
  return shortSessionDate(r.updated_at);
}
function sessionGroupId(r) { return sessionsState.group === 'project' ? r.org + ':' + (r.project_id || '') : sessionGroupLabel(r); }
function renderSessionRows() {
  const rows = filteredSessions(), pages = Math.max(1, Math.ceil(rows.length / 25)); sessionsState.page = Math.min(sessionsState.page, pages - 1);
  const visible = rows.slice(sessionsState.page * 25, (sessionsState.page + 1) * 25), groups = new Map();
  visible.forEach((r,i)=>{const key=sessionGroupId(r);if(!groups.has(key))groups.set(key,[]);groups.get(key).push({r,i});});
  if (!sessionsState.groupsInitialized && groups.size) {
    [...groups.keys()].slice(1).forEach(key=>sessionsState.collapsedGroups.add(key)); sessionsState.groupsInitialized=true;
  }
  $('#sessionRows').innerHTML = [...groups.entries()].map(([key,items],g)=>{
    const r=items[0].r, allRows=rows.filter(v=>sessionGroupId(v)===key), open=Boolean(sessionsState.search || sessionsState.group==='time' || !sessionsState.collapsedGroups.has(key));
    return `<details class="session-group" data-group-index="${g}" ${open?'open':''}><summary>${icon('chevron-right')}<strong>${esc(sessionGroupLabel(r))}</strong><span class="group-project-label">${sessionsState.group==='project'?'Science 项目':''}</span><span class="group-count">${fmt(allRows.length)} 个会话 · ${shortSessionDate(allRows[0].updated_at)}</span></summary>${items.map(({r,i})=>`<article class="session-row" data-session-index="${i}"><button class="session-open" data-session-op="read" aria-label="查看 ${esc(r.title)}"><span class="row-detail"><strong>${esc(r.title)}</strong><span class="session-preview">${sessionsState.group==='time'?esc(r.project_name||'未分组项目')+' · ':''}${r.last_preview?'最后：'+esc(r.last_preview):r.preview?'首条：'+esc(r.preview):fmt(r.messages)+' 条消息'}</span></span><span class="session-row-date">${shortSessionDate(r.updated_at)}</span></button><div class="actions session-row-actions"><button class="icon-btn ${r.starred?'starred':''}" data-session-op="star" aria-label="${r.starred?'取消收藏':'收藏'} ${esc(r.title)}" aria-pressed="${Boolean(r.starred)}" title="收藏">${icon('star')}</button><button class="icon-btn" data-session-op="rename" aria-label="重命名 ${esc(r.title)}" title="重命名">${icon('pencil')}</button><button class="icon-btn" data-session-op="archive" aria-label="${r.archived?'恢复':'归档'} ${esc(r.title)}" title="${r.archived?'恢复':'归档'}">${icon(r.archived?'archive-restore':'archive')}</button></div></article>`).join('')}</details>`;
  }).join('') || `<div class="empty">${icon('messages-square')}<strong>没有匹配的会话</strong><span>切换筛选，或继续研究后刷新。</span></div>`;
  [...document.querySelectorAll('.session-group')].forEach((d,i)=>d.ontoggle=()=>{if(!d.isConnected)return;const key=[...groups.keys()][i];d.open?sessionsState.collapsedGroups.delete(key):sessionsState.collapsedGroups.add(key);});
  $('#sessionPagination').innerHTML = `<div class="pagination"><span>${fmt(rows.length)} 个会话 · 第 ${sessionsState.page + 1} / ${pages} 页</span><div class="actions"><button class="icon-btn" id="sessionPrev" aria-label="上一页" ${sessionsState.page===0?'disabled':''}>${icon('chevron-left')}</button><button class="icon-btn" id="sessionNext" aria-label="下一页" ${sessionsState.page+1===pages?'disabled':''}>${icon('chevron-right')}</button></div></div>`;
  $('#sessionPrev').onclick=()=>{sessionsState.page--;renderSessionRows();};$('#sessionNext').onclick=()=>{sessionsState.page++;renderSessionRows();};
  document.querySelectorAll('[data-session-op]').forEach(b=>b.onclick=()=>action(async()=>{
    const r=visible[Number(b.closest('[data-session-index]').dataset.sessionIndex)],op=b.dataset.sessionOp;
    if(op==='read'){await readSession(r);renderSessions();return;}
    if(op==='rename'){openSessionRename(r);return;}
    await workspaceCall('session_meta',{key:r.key,changes:op==='star'?{starred:!r.starred}:{archived:!r.archived}});
    notice(op==='star'?r.starred?'已取消收藏。':'已收藏会话。':r.archived?'会话已恢复。':'已归档，Science 原始历史保留。');
    await loadSessions();renderSessions();
  }));icons();
}
function transcriptBlocks(m) { return m.blocks?.length ? m.blocks : [{type:'text',text:m.text||''}]; }
function sessionTurns() {
  const turns=[];let turn=null;const pending=new Map();
  for(const m of sessionsState.messages){
    const blocks=transcriptBlocks(m),question=m.role==='user'&&blocks.some(b=>b.type==='text'&&b.text?.trim());
    if(question||!turn){turn={id:Number(m.idx),question:question?m:null,steps:[],replies:[],start:m.timestamp};turns.push(turn);}
    for(const [bi,b] of blocks.entries()){
      const block={...b,idx:Number(m.idx),key:`${m.idx}-${bi}`,timestamp:m.timestamp};
      if(b.type==='tool_use'){
        block.result=null;turn.steps.push(block);pending.set(b.id||block.key,block);
      }else if(b.type==='tool_result'){
        const match=b.tool_use_id?pending.get(b.tool_use_id):[...pending.values()].find(v=>!v.result);
        if(match&&!match.result){match.result=block;}else turn.steps.push(block);
      }else if(b.type==='thinking')turn.steps.push(block);
      else if(!question)turn.replies.push(block);
    }
  }
  return turns;
}
function stepTarget(step){
  if(step.type!=='tool_use')return '';
  try{const input=JSON.parse(step.text);const target=input.command||input.file_path||input.path||input.query||input.pattern||input.url||input.skill||'';return typeof target==='string'?target.replace(/\s+/g,' ').slice(0,180):'';}catch{return '';}
}
function stepContent(text,key,label){
  const value=String(text||''),lines=value.split('\n'),long=lines.length>8||value.length>1800,expanded=sessionsState.expandedSteps.has(key);
  return `<div class="step-content"><span class="step-section-label">${label}</span><pre><code>${esc(long&&!expanded?lines.slice(0,8).join('\n').slice(0,1800):value)}</code></pre>${long?`<button class="text-btn" data-expand-step="${esc(key)}" aria-expanded="${expanded}">${expanded?'收起内容':'显示全部'}${expanded?'':` · ${lines.length} 行`}</button>`:''}</div>`;
}
function renderTimelineStep(step){
  const failed=Boolean(step.is_error||step.result?.is_error),target=stepTarget(step),name=step.name||({thinking:'思考过程',tool_result:'工具结果'}[step.type])||'工具调用';
  return `<details class="message-step timeline-step ${step.type} ${failed?'failed':''}"><summary><span class="step-dot"></span><strong>${esc(name)}</strong>${target?`<span class="step-target" title="${esc(target)}">(${esc(target)})</span>`:''}<span class="step-status">${failed?'失败':step.result?'已返回':''}</span>${icon('chevron-down')}</summary><div class="step-details">${stepContent(step.text,step.key,step.type==='tool_use'?'参数':step.type==='thinking'?'思考':'结果')}${step.result?stepContent(step.result.text,step.result.key,'结果'):''}${step.truncated||step.result?.truncated?'<p class="subtle">消息内容过长，已截断。</p>':''}</div></details>`;
}
function renderSessionReader() {
  const r=sessionsState.selected,box=$('#sessionReader');if(!box||!r)return;
  const rows=filteredSessions(),pos=rows.findIndex(v=>v.key===r.key),time=r.created_at?dateLabel(r.created_at)+(r.updated_at!==r.created_at?' → '+dateLabel(r.updated_at):''):dateLabel(r.updated_at);
  box.innerHTML=`<header class="session-detail-heading"><button class="icon-btn reader-back" id="closeSession" aria-label="关闭会话阅读" title="返回会话列表">${icon('arrow-left')}</button><span class="reader-agent-mark">${icon('sparkles')}</span><h1>${esc(r.title)}</h1><div class="actions"><button class="icon-btn" id="previousSession" aria-label="上一个会话" ${pos<=0?'disabled':''}>${icon('chevron-left')}</button><button class="icon-btn" id="nextSession" aria-label="下一个会话" ${pos<0||pos>=rows.length-1?'disabled':''}>${icon('chevron-right')}</button><button class="icon-btn ${r.starred?'starred':''}" id="readerStar" aria-label="收藏当前会话" aria-pressed="${Boolean(r.starred)}" title="收藏">${icon('star')}</button><button class="icon-btn" id="readerRefresh" aria-label="刷新当前会话" title="刷新会话">${icon('refresh-cw')}</button><button class="icon-btn" id="readerTheme" aria-label="切换深浅主题" title="切换深浅主题">${icon('sun-moon')}</button><button class="icon-btn" id="readerRename" aria-label="重命名当前会话" title="重命名">${icon('pencil')}</button></div></header><div class="session-detail-toolbar"><div class="session-detail-meta"><div><span class="project-chip">${icon('folder-open')}${esc(r.project_name||'未分组项目')}</span>${time?`<span class="session-range">${time}</span>`:''}</div><p id="readerStats"></p></div><div class="reader-controls"><div class="segmented" role="group" aria-label="记录类型">${[['all','全部'],['dialogue','对话'],['changes','改动'],['tools','工具']].map(([key,label])=>`<button data-transcript-filter="${key}" class="${sessionsState.readerFilter===key?'active':''}">${label}</button>`).join('')}</div><div class="actions"><button class="icon-btn" id="expandTimelines" aria-label="展开所有执行过程" title="展开执行过程">${icon('sliders-horizontal')}</button><button class="icon-btn ${sessionsState.outline?'active':''}" id="toggleOutline" aria-label="切换对话目录" aria-pressed="${sessionsState.outline}" title="对话目录">${icon('panel-right')}</button><button class="icon-btn" id="exportSession" aria-label="导出会话 Markdown" title="导出 Markdown">${icon('download')}</button><button class="icon-btn" id="toggleTranscriptSearch" aria-label="搜索当前会话" aria-expanded="${sessionsState.searchOpen}" title="搜索内容">${icon('search')}</button></div></div></div><div class="reader-search search" ${sessionsState.searchOpen?'':'hidden'}>${icon('search')}<input id="transcriptSearch" type="search" aria-label="搜索会话内容" placeholder="搜索已加载的消息内容" value="${esc(sessionsState.readerSearch)}" /></div><div class="reader-content ${sessionsState.outline?'with-outline':''}"><div class="transcript" tabindex="0" aria-label="消息记录"></div><aside id="sessionOutline" class="session-outline" ${sessionsState.outline?'':'hidden'}></aside></div><button class="icon-btn reader-latest" id="latestMessage" aria-label="跳到最新消息" title="跳到最新消息">${icon('arrow-down-to-line')}</button>`;
  $('#closeSession').onclick=()=>{sessionsState.selected=null;renderSessions();};
  $('#readerStar').onclick=()=>action(async()=>{await workspaceCall('session_meta',{key:r.key,changes:{starred:!r.starred}});await loadSessions();renderSessions();});
  $('#readerRename').onclick=()=>openSessionRename(r);
  $('#readerTheme').onclick=()=>$('#themeBtn').click();
  $('#readerRefresh').onclick=()=>action(async()=>{await loadSessions(true);renderSessions();});
  for(const [id,index] of [['previousSession',pos-1],['nextSession',pos+1]])$('#'+id).onclick=()=>action(async()=>{if(rows[index]){await readSession(rows[index]);renderSessions();}});
  $('#exportSession').onclick=()=>action(()=>exportSession(r));
  $('#transcriptSearch').oninput=e=>{sessionsState.readerSearch=e.target.value;renderTranscript();};
  $('#toggleTranscriptSearch').onclick=()=>{sessionsState.searchOpen=!sessionsState.searchOpen;$('.reader-search').hidden=!sessionsState.searchOpen;$('#toggleTranscriptSearch').setAttribute('aria-expanded',String(sessionsState.searchOpen));if(sessionsState.searchOpen)$('#transcriptSearch').focus();else{sessionsState.readerSearch='';$('#transcriptSearch').value='';renderTranscript();}};
  document.querySelectorAll('[data-transcript-filter]').forEach(b=>b.onclick=()=>{sessionsState.readerFilter=b.dataset.transcriptFilter;document.querySelectorAll('[data-transcript-filter]').forEach(v=>v.classList.toggle('active',v===b));renderTranscript();});
  $('#expandTimelines').onclick=()=>{const turns=sessionTurns().filter(t=>t.steps.length),all=turns.every(t=>sessionsState.expandedTimelines.has(t.id));sessionsState.expandedTimelines.clear();if(!all)turns.forEach(t=>sessionsState.expandedTimelines.add(t.id));renderTranscript();};
  $('#toggleOutline').onclick=()=>{sessionsState.outline=!sessionsState.outline;$('.reader-content').classList.toggle('with-outline',sessionsState.outline);$('#sessionOutline').hidden=!sessionsState.outline;$('#toggleOutline').setAttribute('aria-pressed',String(sessionsState.outline));};
  $('#latestMessage').onclick=()=>{const transcript=$('.transcript');transcript.scrollTop=transcript.scrollHeight;};
  renderTranscript();icons();
}
function renderTranscript() {
  const box=$('.transcript');if(!box)return;
  const turns=sessionTurns(),q=sessionsState.readerSearch.trim().toLowerCase(),filter=sessionsState.readerFilter;
  const match=turn=>!q||[...(turn.question?transcriptBlocks(turn.question):[]),...turn.replies,...turn.steps.flatMap(s=>s.result?[s,s.result]:[s])].some(b=>`${b.text} ${b.name||''}`.toLowerCase().includes(q));
  const changed=s=>s.type==='tool_use'&&/(?:edit|write|apply.?patch|file_create|notebook_edit)/i.test(s.name||'');
  const visible=turns.filter(match).map(t=>({...t,steps:filter==='dialogue'?[]:filter==='changes'?t.steps.filter(changed):t.steps,replies:['tools','changes'].includes(filter)?[]:t.replies,question:['tools','changes'].includes(filter)?null:t.question})).filter(t=>t.question||t.steps.length||t.replies.length);
  $('#readerStats').textContent=`${turns.filter(t=>t.question).length} 次提问 · ${turns.reduce((n,t)=>n+t.steps.filter(s=>s.type==='tool_use').length,0)} 次工具调用 · ${sessionsState.selected.model||'Science'}${sessionsState.more?' · 已加载 '+sessionsState.messages.length+'/'+sessionsState.total+' 条消息':''}`;
  box.innerHTML=visible.map(turn=>{
    const failures=turn.steps.filter(s=>s.is_error||s.result?.is_error).length,calls=turn.steps.filter(s=>s.type==='tool_use').length,open=Boolean(q||filter==='tools'||filter==='changes'||failures||sessionsState.expandedTimelines.has(turn.id));
    const question=turn.question?`<article class="message user" id="message-${Number(turn.question.idx)}"><div class="question-meta">${turn.question.timestamp?dateLabel(turn.question.timestamp):''}</div><div class="message-body">${transcriptBlocks(turn.question).filter(b=>['text','attachment'].includes(b.type)).map(b=>b.type==='text'?`<div class="message-text markdown">${markdown(b.text)}</div>`:`<span class="attachment-note">${esc(b.text)}</span>`).join('')}</div></article>`:'';
    const answer=turn.steps.length||turn.replies.length?`<article class="message assistant"><div class="message-role"><span class="message-avatar">${icon('sparkles')}</span><strong>Science</strong>${turn.start?`<span>${dateLabel(turn.start)}</span>`:''}<span class="message-model">${esc(sessionsState.selected.model||'')}</span></div><div class="message-body">${turn.steps.length?`<details class="execution-timeline" data-turn="${turn.id}" ${open?'open':''}><summary>${icon('chevron-right')}<strong>执行过程 · ${turn.steps.length} 步</strong>${calls?`<span>· ${calls} 次工具调用</span>`:''}${failures?`<span class="danger">· ${failures} 个失败</span>`:''}</summary><div class="execution-steps">${turn.steps.map(renderTimelineStep).join('')}</div></details>`:''}${turn.replies.map(b=>b.type==='text'?`<div class="message-text markdown" id="message-${b.idx}">${markdown(b.text)}</div>`:`<div class="attachment-note">${icon('paperclip')}${esc(b.text)}</div>`).join('')}</div></article>`:'';
    return `<section class="session-turn">${question}${answer}</section>`;
  }).join('')||`<div class="empty">${filter==='changes'?'已加载的记录中没有文件改动工具调用。':'没有匹配的消息。'}</div>`;
  if(sessionsState.more)box.insertAdjacentHTML('beforeend',`<div class="load-more"><p class="subtle">已加载 ${sessionsState.messages.length} / ${sessionsState.total} 条消息</p><button class="btn" id="moreMessages">加载更多消息</button></div>`);
  $('#sessionOutline').innerHTML=`<div class="outline-heading">对话目录 <span>${visible.filter(t=>t.question).length}</span></div>${visible.filter(t=>t.question).map(t=>`<button data-message-jump="${Number(t.question.idx)}"><span>提问 #${Number(t.question.idx)+1}</span><strong>${esc(transcriptBlocks(t.question).find(b=>b.type==='text')?.text.slice(0,100))}</strong></button>`).join('')||'<p class="subtle">当前筛选没有提问。</p>'}`;
  document.querySelectorAll('[data-message-jump]').forEach(b=>b.onclick=()=>document.getElementById('message-'+b.dataset.messageJump)?.scrollIntoView({block:'start',behavior:'smooth'}));
  document.querySelectorAll('[data-expand-step]').forEach(b=>b.onclick=()=>{const key=b.dataset.expandStep,y=box.scrollTop;sessionsState.expandedSteps.has(key)?sessionsState.expandedSteps.delete(key):sessionsState.expandedSteps.add(key);const parent=b.closest('.message-step'),wasOpen=parent.open;renderTranscript();if(wasOpen){const newButton=document.querySelector(`[data-expand-step="${key}"]`);if(newButton)newButton.closest('.message-step').open=true;}box.scrollTop=y;});
  document.querySelectorAll('[data-turn]').forEach(d=>d.ontoggle=()=>{if(!d.isConnected)return;d.open?sessionsState.expandedTimelines.add(Number(d.dataset.turn)):sessionsState.expandedTimelines.delete(Number(d.dataset.turn));});
  $('#moreMessages')?.addEventListener('click',()=>action(async()=>{const y=box.scrollTop,r=sessionsState.selected;const data=await workspaceCall('session_messages',{key:r.key,offset:sessionsState.messages.length});sessionsState.messages.push(...(data.messages||[]));sessionsState.more=data.has_more;sessionsState.total=data.total;renderTranscript();$('.transcript').scrollTop=y;}));
  bindMarkdown(box);icons();
}
async function exportSession(r) {
  const messages=[];let offset=0,more=true;
  while(more){const data=await workspaceCall('session_messages',{key:r.key,offset});if(!data.messages.length)break;messages.push(...data.messages);offset+=data.messages.length;more=data.has_more;}
  const text=`# ${r.title}\n\n项目：${r.project_name||'未分组项目'}\n模型：${r.model||'未知'}\n\n`+messages.map(m=>`## ${{user:'你',assistant:'Science',system:'系统'}[m.role]||m.role}\n\n${m.text}${m.truncated?'\n\n[消息过长，已截断]':''}`).join('\n\n---\n\n');
  download(`csswitch-session-${r.id}.md`,text,'text/markdown');notice('完整会话已导出。');
}
function openSessionRename(r) {
  const dialog=$('#workspaceDialog');
  dialog.innerHTML=`<form id="sessionNameForm"><div class="dialog-heading"><h2 id="workspaceTitle">会话显示名称</h2></div><div class="reader-body"><div class="field"><label for="sessionName">名称</label><input id="sessionName" value="${esc(r.title)}" maxlength="200" required /></div><p class="subtle">只调整 CSSwitch 中的显示名称，Science 原始会话保留。</p><p class="err" id="sessionNameError" role="alert" hidden></p></div><div class="dialog-actions"><button type="button" class="btn" id="cancelSessionName">取消</button><button class="btn primary">保存名称</button></div></form>`;
  $('#cancelSessionName').onclick=()=>dialog.close();
  $('#sessionNameForm').onsubmit=e=>{e.preventDefault();action(async()=>{e.target.inert=true;try{await workspaceCall('session_meta',{key:r.key,changes:{title:$('#sessionName').value.trim()}});await loadSessions();renderSessions();dialog.close();notice('会话显示名称已保存。');}catch(err){$('#sessionNameError').hidden=false;$('#sessionNameError').textContent=String(err.message||err);}finally{e.target.inert=false;}});};dialog.showModal();icons();
}
