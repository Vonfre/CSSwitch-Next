// Discovery is the catalog; private copies are created only when distributing.
const agentLibrary = { skills: [], sources: [], path: '', error: '', search: '', source: 'all', status: 'all', selected: new Set(), target: 'science', method: 'auto', page: 0, report: [] };
async function loadAgentLibrary() {
  try {
    const result = await workspaceCall('skill_library_list');
    agentLibrary.skills = result.skills || []; agentLibrary.sources = result.sources || [];
    agentLibrary.path = result.path || ''; agentLibrary.error = '';
  } catch (e) { agentLibrary.skills = []; agentLibrary.selected.clear(); agentLibrary.error = String(e.message || e); }
}
function libraryTargets() {
  return agentLibrary.sources.filter(s => s.target && (s.detected || agentLibrary.skills.some(k => k.targets?.[s.id])));
}
function unifiedSkills() {
  const groups = new Map();
  for (const skill of [...skillsState.discovered, ...agentLibrary.skills]) {
    if (!groups.has(skill.name)) groups.set(skill.name, { name:skill.name, variants:[] });
    const group = groups.get(skill.name); group.variants.push(skill);
    if (skill.source === 'library') group.libraryItem = skill;
  }
  if (agentLibrary.target === 'science') for (const skill of skillsState.installed) {
    if (!groups.has(skill.name)) groups.set(skill.name, {name:skill.name, variants:[]});
    groups.get(skill.name).scienceItem = skill;
  }
  return [...groups.values()].map(group => {
    const variants = agentLibrary.source === 'all' ? group.variants : group.variants.filter(s => s.source === agentLibrary.source || s.locations?.some(l => l.source === agentLibrary.source));
    const preferred = variants.find(s => s.source === 'library') || variants[0] || group.scienceItem;
    const locations = group.variants.flatMap(s => s.locations || (s.source !== 'library' && s.path ? [{source:s.source,key:s.key,path:s.path}] : []));
    return { ...preferred, ...group, locations, key:preferred?.key, description:preferred?.description || '', displayName:preferred?.displayName,
      sourceMatches:agentLibrary.source === 'all' || variants.length > 0 || agentLibrary.source === 'science' && Boolean(group.scienceItem) };
  });
}
function libraryTargetState(skill) {
  if (agentLibrary.target === 'science') {
    const installed = installedSkill(skill);
    if (skillsState.error) return {status:'unknown', message:'请启动 Science 后刷新以读取实际状态。'};
    if (!installed) return {status:'off', installed:false};
    return {status:installed.enabled === false || installed.admin_blocked ? 'off':'on', installed:true, method:'native', blocked:Boolean(installed.admin_blocked)};
  }
  const owned = skill.libraryItem?.targets?.[agentLibrary.target];
  if (owned?.status === 'conflict') return {...owned, blocked:true};
  const locations = skill.locations.filter(l => l.source === agentLibrary.target);
  const active = locations.filter(l => !l.disabled);
  if (active.length) return {status:'on', installed:true, method:owned?.status === 'on' ? owned.method : active.some(l => l.linked) ? 'original-link':'original'};
  if (owned?.status === 'on') return {...owned, installed:true};
  return {status:'off', installed:locations.length > 0, paused:locations.length > 0};
}
function libraryBlocked(skill) {
  const status = libraryTargetState(skill);
  if (status.blocked || status.status === 'unknown') return true;
  if (agentLibrary.target === 'science') return installedSkill(skill) ? Boolean(skillsState.error) : skillsImportBlocked() || !skill.key;
  return status.status !== 'on' && !status.paused && (!skill.key || Boolean(skill.error));
}
function filteredLibrarySkills() {
  return unifiedSkills().filter(s => s.sourceMatches && skillNameMatches(s, agentLibrary.search) &&
    (agentLibrary.status === 'all' || agentLibrary.status === libraryTargetState(s).status))
    .sort((a,b) => Number(Boolean(libraryTargetState(a).installed)) - Number(Boolean(libraryTargetState(b).installed)));
}
function renderAgentLibrary() {
  const targets = libraryTargets();
  if (agentLibrary.target !== 'science' && !targets.some(s => s.id === agentLibrary.target)) agentLibrary.target = 'science';
  const sourceIds = [...new Set(skillsState.discovered.flatMap(s => [s.source, ...(s.locations || []).map(l => l.source)]))];
  if (agentLibrary.skills.length) sourceIds.push('library');
  if (skillsState.installed.length) sourceIds.push('science');
  if (agentLibrary.source !== 'all' && !sourceIds.includes(agentLibrary.source)) agentLibrary.source = 'all';
  const sourceCount = id => new Set((id === 'library' ? agentLibrary.skills : id === 'science' ? skillsState.installed : skillsState.discovered.filter(s => s.source === id || s.locations?.some(l => l.source === id))).map(s => s.name)).size;
  const target = targets.find(s => s.id === agentLibrary.target);
  const total = target?.count ?? 0, paused = target?.pausedCount ?? 0;
  const summary = agentLibrary.target === 'science' ? skillsState.error ? 'Science 未连接 · 请先启动后刷新' : `${skillsState.installed.filter(s => s.enabled !== false && !s.admin_blocked).length} 个已启用` : `${total} 个已启用${paused ? ` · ${paused} 个已停用` : ''}`;
  $('#content').innerHTML = `<div class="skills-page">${skillsChrome()}
    <div class="library-destination"><label>安装目标<select id="libraryTarget" aria-label="管理目标"><option value="science" ${agentLibrary.target === 'science' ? 'selected' : ''}>Claude Science${skillsState.error ? ' · 未连接' : ` · ${skillsState.installed.length} 个`}</option>${targets.map(s => `<option value="${esc(s.id)}" ${s.id === agentLibrary.target ? 'selected' : ''}>${esc(s.name)} · ${s.count || 0} 个</option>`).join('')}</select></label><span class="subtle" id="libraryTargetSummary">${esc(summary)}</span>${skillsNotices()}<div class="skills-target-action"><button class="btn primary" id="libraryEnableAll">${icon('power')}一键启用全部</button></div></div>
    <div class="filter-toolbar unified-filters"><div class="search">${icon('search')}<input id="librarySearch" aria-label="搜索统一技能库" type="search" placeholder="搜索技能名称" value="${esc(agentLibrary.search)}" /></div><select id="librarySource" aria-label="技能来源"><option value="all">全部来源</option>${sourceIds.map(id => `<option value="${esc(id)}" ${id === agentLibrary.source ? 'selected' : ''}>${esc(skillSources[id] || agentLibrary.sources.find(s => s.id === id)?.name || id)} · ${sourceCount(id)} 个</option>`).join('')}</select><select id="libraryStatus" aria-label="目标启用状态">${[['all','全部状态'],['on','已启用'],['off','未启用']].map(([id,label]) => `<option value="${id}" ${agentLibrary.status === id ? 'selected' : ''}>${label}</option>`).join('')}</select></div>
    <div id="libraryBatchBar" class="batch-bar"></div><div class="skill-table">${skillTableHeading()}<div id="libraryRows" class="management-list"></div></div><div id="libraryPages"></div><div id="libraryReport"></div>
    <details class="library-storage"><summary>分发方式与说明</summary><label class="library-method">新增安装<select id="libraryMethod" aria-label="同步方式" ${agentLibrary.target === 'science' ? 'disabled' : ''}>${[['auto','自动（优先软链接）'],['symlink','软链接'],['copy','复制']].map(([v,n]) => `<option value="${v}" ${v === agentLibrary.method ? 'selected' : ''}>${n}</option>`).join('')}</select></label><p>已有技能保持原内容。新增分发自动建立私有副本：<code>${esc(agentLibrary.path)}</code>。Science 使用原生接口安装与启停。本机目录开关不改变其他共享搜索路径，部分 Agent 需重新加载技能列表。</p></details></div>`;
  bindSkillsChrome();
  $('#librarySearch').oninput = e => { agentLibrary.search = e.target.value; agentLibrary.page = 0; renderLibraryRows(); };
  $('#librarySource').onchange = e => { agentLibrary.source = e.target.value; agentLibrary.page = 0; agentLibrary.selected.clear(); renderLibraryRows(); };
  $('#libraryStatus').onchange = e => { agentLibrary.status = e.target.value; agentLibrary.page = 0; renderLibraryRows(); };
  $('#libraryTarget').onchange = e => { agentLibrary.target = e.target.value; agentLibrary.page = 0; agentLibrary.selected.clear(); renderAgentLibrary(); };
  $('#libraryMethod').onchange = e => { agentLibrary.method = e.target.value; };
  $('#libraryEnableAll').onclick = () => action(() => runLibraryBatch(filteredLibrarySkills().filter(s => libraryTargetState(s).status !== 'on'), 'enable'));
  renderLibraryRows(); renderLibraryReport(); icons();
}
function renderLibraryRows() {
  const filtered = filteredLibrarySkills();
  const pages = Math.max(1, Math.ceil(filtered.length / 50)); agentLibrary.page = Math.min(agentLibrary.page, pages - 1);
  const visible = filtered.slice(agentLibrary.page * 50, (agentLibrary.page + 1) * 50);
  const pending = filtered.filter(s => libraryTargetState(s).status !== 'on');
  $('#libraryEnableAll').disabled = !pending.some(s => !libraryBlocked(s));
  $('#libraryEnableAll').innerHTML = `${icon('power')}${pending.length ? `一键启用 ${pending.length} 个` : '全部已启用'}`;
  $('#libraryRows').innerHTML = visible.map((s, i) => {
    const status = libraryTargetState(s), blocked = libraryBlocked(s);
    const sources = [...new Set(s.variants.flatMap(v => v.locations?.length ? v.locations.map(l => l.source) : [v.source]))];
    const label = status.status === 'unknown' ? '未连接' : status.status === 'conflict' ? '文件已改变' : status.status === 'on' ? '已启用' : status.installed ? '已停用' : '未安装';
    return `<article class="management-row skill-row library-row ${agentLibrary.selected.has(s.name) ? 'is-selected' : ''}" data-library-row="${i}"><input class="row-check" type="checkbox" data-library-select="${i}" aria-label="选择统一技能 ${esc(s.name)}" ${agentLibrary.selected.has(s.name) ? 'checked' : ''} /><div class="row-detail"><h3><span class="skill-glyph">${icon('book-open')}</span>${esc(s.displayName || s.name)}</h3><p title="${esc(s.description)}">${esc(s.description || s.error || '暂无描述')}</p></div><div class="skill-origin"><span>${esc(sources.map(id => skillSources[id] || agentLibrary.sources.find(a => a.id === id)?.name || id).join(' · ') || 'Science')}</span><span class="status-badge">${label}${status.message ? help(s.name + ' 状态', status.message) : ''}</span></div><div class="actions"><button class="icon-btn" data-library-action="content" aria-label="查看统一技能 ${esc(s.name)}" title="查看内容">${icon('file-text')}</button>${s.libraryItem ? `<button class="icon-btn" data-library-action="update" aria-label="同步来源 ${esc(s.name)}" title="同步原来源" ${s.libraryItem.error || s.libraryItem.locallyModified ? 'disabled' : ''}>${icon('refresh-cw')}</button>` : ''}<button class="switch" role="switch" aria-label="在目标启用 ${esc(s.name)}" aria-checked="${status.status === 'on'}" data-library-action="toggle" ${blocked ? 'disabled' : ''}><span></span></button></div></article>`;
  }).join('') || `<div class="empty">${icon('library')}<strong>没有匹配的技能</strong><span>调整筛选，或在「本机目录」中添加 Agent 的 Skills 位置。</span></div>`;
  document.querySelectorAll('[data-library-select]').forEach(b => b.onchange = () => { const name = visible[Number(b.dataset.librarySelect)].name; b.checked ? agentLibrary.selected.add(name) : agentLibrary.selected.delete(name); b.closest('article').classList.toggle('is-selected', b.checked); renderLibraryBatchBar(visible); });
  document.querySelectorAll('[data-library-action]').forEach(b => b.onclick = () => action(async () => {
    const skill = visible[Number(b.closest('[data-library-row]').dataset.libraryRow)], op = b.dataset.libraryAction;
    if (op === 'content') { const result = skill.key ? await workspaceCall('skill_library_preview', {key:skill.key}) : await mcpCall('skills_content', {name:skill.name}); openTextReader(skill.name, result.content); return; }
    await runLibraryBatch([skill], op === 'toggle' ? libraryTargetState(skill).status === 'on' ? 'disable' : 'enable' : op);
  }));
  renderSkillsPagination($('#libraryPages'), 'library', filtered.length, agentLibrary.page, page => { agentLibrary.page = page; renderLibraryRows(); });
  renderLibraryBatchBar(visible); icons();
}
function renderLibraryBatchBar(visible) {
  const selected = unifiedSkills().filter(s => agentLibrary.selected.has(s.name));
  $('#libraryBatchBar').innerHTML = `<label class="select-all"><input id="librarySelectAll" type="checkbox" aria-label="全选当前页统一技能" ${visible.length && visible.every(s => agentLibrary.selected.has(s.name)) ? 'checked' : ''} />${selected.length ? `已选 ${selected.length} 个` : '全选当前页'}</label><div class="actions">${selected.length ? '<button class="btn" data-library-batch="enable">启用所选</button><button class="btn" data-library-batch="disable">停用所选</button>' : help('批量操作', '一键启用作用于当前筛选的所有页，未安装的技能优先展示。安装后可逐项关闭。勾选技能后可批量操作，全选仅选择当前页。')}</div>`;
  $('#librarySelectAll').indeterminate = visible.some(s => agentLibrary.selected.has(s.name)) && !visible.every(s => agentLibrary.selected.has(s.name));
  $('#librarySelectAll').onchange = e => { visible.forEach(s => e.target.checked ? agentLibrary.selected.add(s.name) : agentLibrary.selected.delete(s.name)); renderLibraryRows(); };
  document.querySelectorAll('[data-library-batch]').forEach(b => b.onclick = () => action(async () => {
    if (b.dataset.libraryBatch === 'disable' && !await confirmAction(`停用所选 ${selected.length} 个技能？`, '只作用于当前安装目标，原有技能保留停用备份，可再次开启。')) return;
    await runLibraryBatch(selected, b.dataset.libraryBatch);
  }));
}
async function runLibraryBatch(items, op) {
  const target = agentLibrary.target, method = agentLibrary.method;
  agentLibrary.report = [];
  for (const [i, s] of items.entries()) {
    const progress = $('#libraryReport');
    if (progress) progress.innerHTML = `<p class="subtle" role="status">正在处理 ${i + 1} / ${items.length} · ${esc(s.name)}</p>`;
    try {
      let result;
      if (op === 'update') result = await workspaceCall('skill_library_update', {name:s.name, expectedFingerprint:s.libraryItem?.currentFingerprint});
      else if (target === 'science') {
        if (skillsState.error) throw new Error('请先启动 Science 并刷新。');
        const existing = installedSkill(s);
        if (existing) {
          if (existing.admin_blocked) throw new Error('此技能受 Science 组织策略限制。');
          result = await mcpCall('skills_enabled', {name:s.name, enabled:op === 'enable'});
        } else if (op === 'enable') {
          if (skillsImportBlocked()) throw new Error('Science 当前不允许安装自定义 Skills。');
          if (!s.key) throw new Error('没有可安装的本机或仓库来源。');
          if (s.key !== 'library:' + s.name) await workspaceCall('skill_library_import', {key:s.key});
          result = await mcpCall('skills_import', await workspaceCall('skill_bundle', {key:'library:' + s.name}));
        } else result = {message:'此目标未安装该技能。'};
      } else result = await workspaceCall('skill_library_toggle', {name:s.name, key:s.key, target, enabled:op === 'enable', method});
      agentLibrary.report.push({name:s.name, status:'ok', message:result.message || '已完成'});
    } catch (e) { agentLibrary.report.push({name:s.name, status:'error', message:String(e.message || e)}); }
  }
  agentLibrary.selected.clear();
  await loadSkills(); skillsState.tab = 'library'; renderSkills();
  const errors = agentLibrary.report.filter(r => r.status === 'error').length;
  notice(`${items.length - errors} 项完成${errors ? `，${errors} 项未完成，请查看结果` : ''}。`, Boolean(errors));
}
function renderLibraryReport() {
  $('#libraryReport').innerHTML = agentLibrary.report.length ? `<details class="workspace-warning" ${agentLibrary.report.some(r => r.status === 'error') ? 'open' : ''}><summary>处理结果 · ${agentLibrary.report.length} 项</summary>${agentLibrary.report.map(r => `<p class="${r.status === 'error' ? 'err' : ''}"><strong>${esc(r.name)}</strong> · ${esc(r.message)}</p>`).join('')}</details>` : '';
}
async function openAgentDirectories() {
  const dialog = $('#workspaceDialog');
  const result = await workspaceCall('agent_sources');
  dialog.innerHTML = `<div class="dialog-heading"><h2 id="workspaceTitle">本机 Agent 目录</h2><button class="icon-btn" id="closeAgentDirectories" aria-label="关闭本机目录">${icon('x')}</button></div><div class="workspace-body agent-directory-body"><p class="subtle">自动检查常见 Skills 位置，识别复制目录和软链接。也可添加自定义 Agent 目录。</p><div class="agent-directories">${(result.sources || []).map(s => `<div class="agent-directory"><strong>${esc(s.name)}</strong><span>${s.exists ? `${s.count || 0} 个技能${s.links ? ` · ${s.links} 个链接` : ''}` : '未发现目录'}</span><code title="${esc(s.path)}">${esc(s.path)}</code>${s.custom ? `<button class="btn quiet" data-remove-agent-source="${esc(s.id)}">移除目录</button>` : ''}</div>`).join('')}</div><details><summary>添加自定义目录</summary><form id="agentDirectoryForm" class="agent-directory-form"><div class="field"><label for="agentName">Agent 名称</label><input id="agentName" required maxlength="60" placeholder="我的 Agent" /></div><div class="field"><label for="agentPath">Skills 目录</label><input id="agentPath" required placeholder="/Users/用户名/.agent/skills" /></div><button class="btn primary" type="submit">添加并扫描</button><p id="agentDirectoryError" class="err" role="alert"></p></form></details></div>`;
  $('#closeAgentDirectories').onclick = () => dialog.close();
  document.querySelectorAll('[data-remove-agent-source]').forEach(b => b.onclick = () => action(async () => {
    if (!await confirmAction('移除这个发现目录？', '只从发现列表移除，磁盘文件保留；有已分发技能时需先停用。')) return;
    await workspaceCall('agent_source_remove', { id: b.dataset.removeAgentSource }); await loadSkills(); renderSkills(); await openAgentDirectories();
  }));
  $('#agentDirectoryForm').onsubmit = async e => {
    e.preventDefault(); const form = e.target; form.inert = true;
    try { await workspaceCall('agent_source_add', { name: $('#agentName').value, path: $('#agentPath').value }); await loadSkills(); renderSkills(); await openAgentDirectories(); }
    catch (error) { $('#agentDirectoryError').textContent = String(error.message || error); }
    finally { form.inert = false; }
  };
  if (!dialog.open) dialog.showModal(); icons();
}
