// Science owns installed skills; CSSwitch keeps the source of managed local drafts.
const skillsState = { tab: 'library', search: '', source: 'all', status: 'all', page: 0, selected: new Set(), installed: [], discovered: [], repos: [], error: '', degraded: false, localImportAvailable: false, customAllowed: true, warnings: [], updates: {}, checked: false, report: [], importFiles: [] };
const skillSources = { library:'统一副本', science:'Science', anthropic: 'Science 内置', bundled: 'Science 内置', personal: '个人 Skill', organization: '组织 Skill', imported: '已导入', 'csswitch-local': 'Science 本地', draft: '本地草稿', local: '本地 Skill', codex: 'Codex 本地', agents: '共享本地', claude: 'Claude 本地', github: 'GitHub 仓库' };
const workspaceCall = (operation, params = {}) => call('workspace_manage', { operation, params });
const skillId = s => skillsState.tab === 'installed' ? s.name : s.key;
function skillsImportBlocked() { return !native() || Boolean(skillsState.error) || !skillsState.customAllowed || (skillsState.degraded && !skillsState.localImportAvailable); }
function installedSkill(s) { return skillsState.installed.find(v => v.name === s.name); }
function removableSkill(s) { return !s.admin_blocked && skillsState.customAllowed && ['csswitch-local', 'local', 'draft', 'personal', 'imported', 'custom'].includes(s.source) && (s.source === 'csswitch-local' || !skillsState.degraded); }
function skillOrigin(s) {
  if (s.sourceKey) return [...skillsState.discovered, ...agentLibrary.skills].find(d => d.key === s.sourceKey || d.locations?.some(l => l.key === s.sourceKey));
  const candidates = skillsState.discovered.filter(d => d.name === s.name);
  return candidates.length === 1 ? candidates[0] : null;
}
async function loadSkills() {
  skillsState.error = ''; skillsState.degraded = false; skillsState.localImportAvailable = false; skillsState.customAllowed = true; skillsState.warnings = [];
  skillsState.installed = []; skillsState.discovered = []; skillsState.repos = [];
  if (!native()) { skillsState.error = '请在桌面应用中管理 Skills。'; return; }
  const [catalog, discovery] = await Promise.allSettled([mcpCall('skills_list'), workspaceCall('skills_discover'), loadAgentLibrary()]);
  if (catalog.status === 'fulfilled') {
    skillsState.installed = (catalog.value.skills || []).filter(s => !s.userHidden);
    skillsState.degraded = Boolean(catalog.value.degraded);
    skillsState.localImportAvailable = Boolean(catalog.value.localImportAvailable);
    skillsState.customAllowed = catalog.value.customAllowed !== false;
    if (skillsState.degraded) skillsState.warnings.push(skillsState.localImportAvailable ? '云端目录离线 · 本地 Skills 仍可安装、更新并绑定到 OPERON。' : (catalog.value.localImportReason || 'Science 目录暂不可用，当前无法导入；请启动第三方 Science 后刷新。'));
    if (!skillsState.customAllowed) skillsState.warnings.push('Science 组织策略禁止添加自定义 Skills。');
  } else skillsState.error = String(catalog.reason.message || catalog.reason);
  if (discovery.status === 'fulfilled') {
    skillsState.discovered = discovery.value.skills || []; skillsState.repos = discovery.value.repos || [];
    skillsState.warnings.push(...(discovery.value.warnings || []));
    for (const source of discovery.value.sources || []) skillSources[source.id] = source.name;
  } else skillsState.warnings.push(String(discovery.reason.message || discovery.reason));
  const present = new Set((skillsState.tab === 'installed' ? skillsState.installed : skillsState.discovered).map(skillId));
  skillsState.selected = new Set([...skillsState.selected].filter(id => present.has(id)));
}
function skillNameMatches(skill, query) {
  const q = query.trim().toLowerCase();
  return [skill.name, skill.displayName].some(name => typeof name === 'string' && name.toLowerCase().includes(q));
}
function renderSkillsPagination(container, prefix, total, page, onPage) {
  const pages = Math.max(1, Math.ceil(total / 50));
  const numbers = [...new Set([0, pages - 1, page - 1, page, page + 1])].filter(n => n >= 0 && n < pages).sort((a, b) => a - b);
  const buttons = numbers.map((n, i) => `${i && n > numbers[i - 1] + 1 ? '<span class="page-gap" aria-hidden="true">…</span>' : ''}<button class="btn page-number" data-page="${n}" aria-label="第 ${n + 1} 页" ${n === page ? 'aria-current="page"' : ''}>${n + 1}</button>`).join('');
  container.innerHTML = `<div class="pagination skills-pagination"><span>${fmt(total)} 个技能 · 第 ${page + 1} / ${pages} 页</span><div class="skills-page-controls"><nav class="page-numbers" aria-label="技能列表分页"><button class="btn" id="${prefix}Prev" data-page="${page - 1}" ${page === 0 ? 'disabled' : ''}>上一页</button>${buttons}<button class="btn" id="${prefix}Next" data-page="${page + 1}" ${page + 1 >= pages ? 'disabled' : ''}>下一页</button></nav>${pages > 1 ? `<form class="page-jump"><label>跳至<input type="number" inputmode="numeric" min="1" max="${pages}" step="1" required aria-label="跳转页码" value="${page + 1}" /></label><button class="btn" type="submit">跳转</button></form>` : ''}</div></div>`;
  const go = value => {
    if (!Number.isInteger(value) || value < 0 || value >= pages) return;
    onPage(value);
    container.querySelector(`[data-page="${value}"]`)?.focus({ preventScroll: true });
  };
  container.querySelectorAll('[data-page]').forEach(button => button.onclick = () => go(Number(button.dataset.page)));
  const form = container.querySelector('form');
  if (form) form.onsubmit = event => { event.preventDefault(); go(Number(form.querySelector('input').value) - 1); };
}
function filteredSkills() {
  const installed = skillsState.tab === 'installed', q = skillsState.search.trim().toLowerCase();
  const installedNames = new Set(skillsState.installed.map(s => s.name));
  const rows = (installed ? skillsState.installed : skillsState.discovered).filter(s =>
    (skillsState.source === 'all' || s.source === skillsState.source || s.locations?.some(l => l.source === skillsState.source)) &&
    skillNameMatches(s, q) &&
    (skillsState.status === 'all' || skillsState.status === 'updates' && skillsState.updates[s.name]?.available || skillsState.status === 'enabled' && s.enabled !== false || skillsState.status === 'disabled' && s.enabled === false || skillsState.status === 'new' && !installedNames.has(s.name)));
  return installed ? rows : rows.sort((a, b) => Number(installedNames.has(a.name)) - Number(installedNames.has(b.name)));
}
function skillsChrome() {
  return `<div class="skills-header"><div class="management-toolbar page-tabs"><div role="group" aria-label="Skills 视图"><button data-skill-tab="library" class="${skillsState.tab === 'library' ? 'active' : ''}">统一技能库 <span class="label-count">${unifiedSkills().length}</span></button><button data-skill-tab="installed" class="${skillsState.tab === 'installed' ? 'active' : ''}">Science 已安装 <span class="label-count">${skillsState.installed.length}</span></button></div></div><div class="actions skills-tools"><button class="btn quiet" id="agentDirectories">${icon('folder-search')}本机目录</button><button class="btn quiet" id="skillRepos">${icon('git-branch')}仓库管理</button><button class="icon-btn" id="checkSkillUpdates" aria-label="检查更新" title="检查 Science 技能更新" ${skillsImportBlocked() ? 'disabled' : ''}>${icon('refresh-cw')}</button><button class="btn" id="importSkill" title="导入技能到 Science" ${skillsImportBlocked() ? 'disabled' : ''}>${icon('plus')}导入 Skill</button><input id="skillFile" type="file" accept=".zip,.skill,.md" multiple hidden /></div></div>`;
}
function skillsNotices() {
  const messages = [...(skillsState.error ? [skillsState.error] : []), ...skillsState.warnings, ...(agentLibrary.error ? [agentLibrary.error] : [])];
  return messages.length ? `<details class="skills-notice"><summary aria-label="技能状态提示" title="查看技能状态提示">${icon('info')}<span>${skillsState.degraded && skillsState.localImportAvailable ? '本地可用' : '状态提示'}</span></summary><div>${messages.map(text => `<p>${esc(text)}</p>`).join('')}</div></details>` : '';
}
function skillTableHeading() { return '<div class="skill-table-head"><span></span><span>技能 / 描述</span><span>来源 / 状态</span><span>操作</span></div>'; }
function bindSkillsChrome() {
  document.querySelectorAll('[data-skill-tab]').forEach(b => b.onclick = () => { skillsState.tab = b.dataset.skillTab; renderSkills(); });
  $('#skillRepos').onclick = openSkillRepos;
  $('#agentDirectories').onclick = () => action(openAgentDirectories);
  $('#checkSkillUpdates').onclick = () => action(checkSkillUpdates);
  $('#importSkill').onclick = () => $('#skillFile').click();
  $('#skillFile').onchange = e => {
    const files = [...e.target.files]; e.target.value = ''; if (!files.length) return;
    if (files.length > 50) { notice('一次最多导入 50 个技能包。', true); return; }
    openSkillFileImport(files);
  };
}
function renderSkills() {
  if (skillsState.tab !== 'installed') { renderAgentLibrary(); return; }
  const installed = skillsState.tab === 'installed', rows = installed ? skillsState.installed : skillsState.discovered;
  const sources = [...new Set(rows.flatMap(s => [s.source, ...(s.locations || []).map(l => l.source)]))], nUpdates = Object.values(skillsState.updates).filter(v => v.available).length;
  $('#content').innerHTML = `<div class="skills-page">${skillsChrome()}
    <div class="library-destination"><label>安装目标<select aria-label="管理目标" disabled><option>Claude Science · ${skillsState.installed.length} 个</option></select></label><span class="subtle" title="Science 中的实际启用状态">${skillsState.installed.filter(s => s.enabled !== false && !s.admin_blocked).length} 个已启用${nUpdates ? ` · ${nUpdates} 个可更新` : skillsState.checked ? ' · 已检查更新' : ''}</span>${skillsNotices()}<div class="skills-target-action">${nUpdates ? `<button class="btn" id="updateAllSkills" ${skillsImportBlocked() ? 'disabled' : ''}>全部更新</button>` : ''}</div></div>
    <div class="filter-toolbar skill-filters"><div class="search">${icon('search')}<input id="skillSearch" type="search" placeholder="搜索技能名称" aria-label="搜索 Skills" value="${esc(skillsState.search)}" /></div><select id="skillSource" aria-label="Skills 来源"><option value="all">全部来源</option>${sources.map(s => `<option value="${esc(s)}" ${skillsState.source === s ? 'selected' : ''}>${esc(skillSources[s] || s)}</option>`).join('')}</select><select id="skillStatus" aria-label="Skills 状态"><option value="all">${installed ? '全部状态' : '全部状态 · 未安装优先'}</option>${(installed ? [['updates','可更新'],['enabled','已启用'],['disabled','已停用']] : [['new','未安装']]).map(([v,label]) => `<option value="${v}" ${skillsState.status === v ? 'selected' : ''}>${label}</option>`).join('')}</select></div>
    <div id="skillBatchBar" class="batch-bar"></div><div class="skill-table">${skillTableHeading()}<div id="skillRows" class="management-list"></div></div><div id="skillPagination"></div><div id="skillReport"></div></div>`;
  bindSkillsChrome();
  $('#skillSearch').oninput = e => { skillsState.search = e.target.value; skillsState.page = 0; renderSkillRows(); };
  $('#skillSource').onchange = e => { skillsState.source = e.target.value; skillsState.page = 0; renderSkillRows(); };
  $('#skillStatus').onchange = e => { skillsState.status = e.target.value; skillsState.page = 0; renderSkillRows(); };
  $('#updateAllSkills')?.addEventListener('click', () => action(async () => {
    const items = skillsState.installed.filter(s => skillsState.updates[s.name]?.available);
    if (await confirmAction(`更新 ${items.length} 个 Skills？`, '使用安装来源的最新内容，保留启停状态和代理绑定。')) await runSkillBatch(items, 'update');
  }));
  renderSkillRows(); renderSkillReport(); icons();
}
function renderSkillRows() {
  const installed = skillsState.tab === 'installed', rows = filteredSkills();
  const pages = Math.max(1, Math.ceil(rows.length / 50)); skillsState.page = Math.min(skillsState.page, pages - 1);
  const visible = rows.slice(skillsState.page * 50, (skillsState.page + 1) * 50);
  $('#skillRows').innerHTML = visible.map((s, i) => {
    const exists = installedSkill(s), update = skillsState.updates[s.name];
    const blocked = installed ? !native() || Boolean(skillsState.error) || s.admin_blocked : !native();
    const status = installed ? s.admin_blocked ? '组织禁用' : s.locallyModified ? '本地已修改' : update?.available ? '可更新' : s.enabled === false ? '已停用' : '已启用' : exists ? '已安装' : '未安装';
    return `<article class="management-row skill-row ${skillsState.selected.has(skillId(s)) ? 'is-selected' : ''}" data-skill-index="${i}"><input class="row-check" type="checkbox" data-select-skill="${i}" aria-label="选择 ${esc(s.name)}${installed ? '' : `（${esc(skillSources[s.source] || s.source)}）`}" ${skillsState.selected.has(skillId(s)) ? 'checked' : ''} ${blocked ? 'disabled' : ''} /><div class="row-detail"><h3><span class="skill-glyph">${icon('book-open')}</span>${esc(s.displayName || s.name)}</h3><p title="${esc(s.description || '')}">${esc(s.description || '暂无描述')}</p></div><div class="skill-origin"><span>${esc(s.locations?.length ? [...new Set(s.locations.map(l => skillSources[l.source] || l.source))].join(' · ') : skillSources[s.source] || s.source)}</span><span class="status-badge ${status === '可更新' ? 'update' : ''}">${status}</span></div><div class="actions">${installed ? `<button class="icon-btn" data-skill-action="content" aria-label="查看内容" title="查看内容">${icon('file-text')}</button>${s.source === 'csswitch-local' && update?.available ? `<button class="icon-btn" data-skill-action="update" aria-label="更新 ${esc(s.name)}" title="更新" ${skillsImportBlocked() || s.locallyModified || s.admin_blocked ? 'disabled' : ''}>${icon('refresh-cw')}</button>` : ''}${['csswitch-local', 'local', 'draft', 'personal', 'imported', 'custom'].includes(s.source) ? `<button class="icon-btn danger" data-skill-action="remove" ${!removableSkill(s) ? 'disabled' : ''} aria-label="卸载 ${esc(s.name)}" title="卸载">${icon('trash-2')}</button>` : ''}<button class="switch" role="switch" aria-label="启用 ${esc(s.name)}" aria-checked="${s.enabled !== false}" data-skill-action="enabled" ${s.admin_blocked ? 'disabled' : ''}><span></span></button>` : `<button class="icon-btn" data-library-import="${i}" title="加入统一库" aria-label="加入统一库 ${esc(s.name)}">${icon('library')}</button><button class="btn ${exists ? '' : 'primary'}" data-skill-action="install" ${exists || skillsImportBlocked() ? 'disabled' : ''}>${icon(exists ? 'check' : 'plus')}${exists ? '已安装' : '安装到 Science'}</button>`}</div></article>`;
  }).join('') || `<div class="empty">${icon('book-open')}<strong>${skillsState.search || skillsState.status !== 'all' ? '没有匹配的 Skills' : installed ? '建立你的技能库' : '没有发现新技能'}</strong><span>${installed ? '从本机和仓库发现技能，或一次导入多个技能包。' : '扫描本机 Agent 的 Skills 目录和软链接，也可添加自定义目录与 GitHub 仓库。'}</span>${installed ? '<button class="btn" id="discoverSkills">浏览可安装技能</button>' : ''}</div>`;
  document.querySelectorAll('[data-library-import]').forEach(b => b.onclick = () => action(() => runLibraryBatch([visible[Number(b.dataset.libraryImport)]], 'import')));
  $('#discoverSkills')?.addEventListener('click', () => { skillsState.tab = 'library'; renderSkills(); });
  document.querySelectorAll('[data-select-skill]').forEach(b => b.onchange = () => { const id = skillId(visible[Number(b.dataset.selectSkill)]); b.checked ? skillsState.selected.add(id) : skillsState.selected.delete(id); b.closest('article').classList.toggle('is-selected', b.checked); renderSkillBatchBar(visible); });
  document.querySelectorAll('[data-skill-action]').forEach(b => b.onclick = () => action(async () => {
    const s = visible[Number(b.closest('[data-skill-index]').dataset.skillIndex)], op = b.dataset.skillAction;
    if (op === 'content') { const value = await mcpCall('skills_content', { name: s.name }); openTextReader(s.displayName || s.name, value.content || '此技能暂无可读取的内容。'); return; }
    if (op === 'remove' && !await confirmAction(`卸载 ${s.name}？`, '移除 Science 中的技能及代理绑定，原始本机目录和仓库保留。')) return;
    if (op === 'update' && !await confirmAction(`更新 ${s.name}？`, '使用原来源的最新文件，保留启停状态和代理绑定。验证失败时会恢复原内容。')) return;
    await runSkillBatch([s], op === 'enabled' ? s.enabled === false ? 'enable' : 'disable' : op);
  }));
  renderSkillsPagination($('#skillPagination'), 'skill', rows.length, skillsState.page, page => { skillsState.page = page; renderSkillRows(); });
  renderSkillBatchBar(visible); icons();
}
function renderSkillBatchBar(visible) {
  const installed = skillsState.tab === 'installed', n = skillsState.selected.size;
  const eligible = visible.filter(s => installed ? !s.admin_blocked && !skillsState.error : native());
  $('#skillBatchBar').innerHTML = `<label class="select-all"><input type="checkbox" id="selectAllSkills" aria-label="全选当前页 Skills" ${eligible.length && eligible.every(s => skillsState.selected.has(skillId(s))) ? 'checked' : ''} ${!eligible.length ? 'disabled' : ''} /><span>${n ? `已选 ${n} 个` : '全选当前页'}</span></label><div class="actions">${n ? installed ? `<button class="btn quiet" data-skill-batch="enable">批量启用</button><button class="btn quiet" data-skill-batch="disable">批量停用</button><button class="btn quiet" data-skill-batch="update" ${skillsImportBlocked() ? 'disabled' : ''}>${icon('refresh-cw')}更新所选</button><button class="btn quiet danger" data-skill-batch="remove">${icon('trash-2')}卸载所选</button>` : `<button class="btn" id="libraryImportSelected">${icon('library')}加入统一库</button><button class="btn primary" data-skill-batch="install" ${skillsImportBlocked() ? 'disabled' : ''}>${icon('plus')}安装所选</button>` : help('批量操作', '勾选技能后可批量启用、停用、更新或卸载。全选仅选择当前页，已选项可以跨页保留。')}${n ? '<button class="icon-btn" id="clearSkillSelection" aria-label="清除 Skills 选择">' + icon('x') + '</button>' : ''}</div>`;
  $('#libraryImportSelected')?.addEventListener('click', () => action(() => runLibraryBatch(skillsState.discovered.filter(s => skillsState.selected.has(s.key)), 'import')));
  $('#selectAllSkills').indeterminate = eligible.some(s => skillsState.selected.has(skillId(s))) && !eligible.every(s => skillsState.selected.has(skillId(s)));
  $('#selectAllSkills').onchange = e => { eligible.forEach(s => e.target.checked ? skillsState.selected.add(skillId(s)) : skillsState.selected.delete(skillId(s))); renderSkillRows(); };
  $('#clearSkillSelection')?.addEventListener('click', () => { skillsState.selected.clear(); renderSkillRows(); });
  document.querySelectorAll('[data-skill-batch]').forEach(b => b.onclick = () => action(async () => {
    const op = b.dataset.skillBatch, selected = (installed ? skillsState.installed : skillsState.discovered).filter(s => skillsState.selected.has(skillId(s)));
    if (['remove','update'].includes(op) && !await confirmAction(`${op === 'remove' ? '卸载' : '更新'}所选 ${selected.length} 个技能？`, op === 'remove' ? '仅移除可管理的 Science 技能及绑定，原来源保留。' : '更新有差异的已管理技能。已修改、来源不明或组织管理的技能会跳过。')) return;
    await runSkillBatch(selected, op);
  })); icons();
}
async function skillFileBundle(file) {
  if (file.size > 256 * 1024 * 1024) throw new Error('Skill 文件不能超过 256 MB。');
  return { filename: file.name, bundle: await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error('无法读取 Skill 文件。')); reader.readAsDataURL(file); }) };
}
async function runSkillBatch(items, op, options = {}) {
  skillsState.report = [];
  if (op === 'file') {
    const queue = [];
    for (const file of items) {
      try {
        const plan = await workspaceCall('skill_import_plan', await skillFileBundle(file));
        queue.push(...plan.skills);
      } catch(e) { skillsState.report.push({name:file.name,status:'error',message:String(e.message || e)}); }
    }
    items = queue;
  }
  for (const [i, s] of items.entries()) {
    const name = s.name || s.filename || 'Skill'; notice(`正在${({ install:'安装', file:'导入', update:'更新', enable:'启用', disable:'停用', remove:'卸载' })[op]} ${i + 1}/${items.length} · ${name}`);
    try {
      let result;
      if (op === 'install') {
        if (installedSkill(s)) { skillsState.report.push({ name, status:'skip', message:'已安装，未重复导入' }); continue; }
        result = await mcpCall('skills_import', await workspaceCall('skill_bundle', { key:s.key }));
        skillsState.installed.push({ name:result.name || s.name, source:'csswitch-local' });
      } else if (op === 'file') {
        const existing = installedSkill(s), pkg = {filename:s.filename,bundle:s.bundle};
        if (existing && options.updateExisting) {
          if (existing.source !== 'csswitch-local' || existing.sourceKey || existing.admin_blocked || existing.locallyModified) {
            skillsState.report.push({name,status:'skip',message:existing.locallyModified ? '保留本地修改' : '仓库来源请用检查更新；Science 管理的技能无法覆盖'}); continue;
          }
          result = await mcpCall('skills_update', {...pkg,name:s.name,expectedFingerprint:existing.fingerprint});
        } else result = await mcpCall('skills_import', pkg);
      }
      else if (op === 'update') {
        const info = skillsState.updates[s.name], origin = skillOrigin(s);
        if (s.source !== 'csswitch-local' || s.admin_blocked || s.locallyModified || !origin || !info?.available) {
          skillsState.report.push({ name, status:'skip', message:s.locallyModified ? '保留本地修改' : info?.message || '无可用更新，或由 Science 管理' }); continue;
        }
        const pkg = await workspaceCall('skill_bundle', { key:origin.key });
        result = await mcpCall('skills_update', { ...pkg, name:s.name, expectedFingerprint:s.fingerprint });
        delete skillsState.updates[s.name];
      } else {
        if (op === 'remove' ? !removableSkill(s) : s.admin_blocked) { skillsState.report.push({ name, status:'skip', message:'受组织策略或来源限制' }); continue; }
        result = await mcpCall(op === 'remove' ? 'skills_remove' : 'skills_enabled', { name:s.name, ...(op !== 'remove' ? { enabled:op === 'enable' } : {}) });
      }
      skillsState.report.push({ name, status:'ok', message:result.message || '已完成' });
    } catch (e) { skillsState.report.push({ name, status:'error', message:String(e.message || e) }); }
  }
  skillsState.selected.clear(); await loadSkills(); if (op === 'file') skillsState.tab = 'installed'; renderSkills();
  const failed = skillsState.report.filter(r => r.status === 'error');
  const ok = skillsState.report.filter(r => r.status === 'ok').length;
  notice(`${ok} 项完成${failed.length ? `，${failed.length} 项失败：${failed[0].message}` : ''}${skillsState.report.some(r => r.status === 'skip') ? '，部分项目已跳过' : ''}。`, Boolean(failed.length));
}
function renderSkillReport() {
  const box = $('#skillReport'); if (!box || !skillsState.report.length) return;
  box.innerHTML = `<details class="batch-report" open><summary>操作结果 · ${skillsState.report.filter(r => r.status === 'ok').length} 项完成 / ${skillsState.report.length} 项</summary>${skillsState.report.map(r => `<div class="report-row ${r.status}">${icon(r.status === 'ok' ? 'check' : r.status === 'error' ? 'circle-alert' : 'minus')}<strong>${esc(r.name)}</strong><span>${esc(r.message)}</span></div>`).join('')}</details>`; icons();
}
async function checkSkillUpdates() {
  const failures = [];
  for (const [i, repo] of skillsState.repos.entries()) {
    notice(`同步仓库 ${i + 1}/${skillsState.repos.length} · ${repo.repo}`);
    try { await workspaceCall('repo_add', { repo:repo.repo, branch:repo.branch }); }
    catch(e) { failures.push({ name:repo.repo, status:'error', message:String(e.message || e) }); }
  }
  await loadSkills(); skillsState.updates = {}; skillsState.checked = true;
  for (const s of skillsState.installed.filter(s => s.source === 'csswitch-local')) {
    const origin = skillOrigin(s);
    if (!origin || !s.fingerprint) { skillsState.updates[s.name] = { available:false, message:'来源未关联，无法检查更新' }; continue; }
    if (origin.repo && failures.some(f => f.name === origin.repo.split('@')[0])) {
      skillsState.updates[s.name] = { available:false, message:'仓库同步失败，请重试' }; continue;
    }
    try {
      const pkg = await workspaceCall('skill_bundle', { key:origin.key });
      skillsState.updates[s.name] = { available:pkg.fingerprint !== s.fingerprint && !s.locallyModified, message:s.locallyModified ? '保留本地修改' : '内容已是最新' };
    } catch(e) { failures.push({ name:s.name, status:'error', message:String(e.message || e) }); }
  }
  skillsState.report = failures; renderSkills();
  const count = Object.values(skillsState.updates).filter(v => v.available).length;
  notice(`检查完成 · ${count} 个技能可更新${failures.length ? `，${failures.length} 项检查失败` : ''}。`, Boolean(failures.length));
}
function openSkillFileImport(files) {
  const dialog = $('#workspaceDialog');
  dialog.innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">BATCH IMPORT</span><h2 id="workspaceTitle">导入 ${files.length} 个 Skills 文件</h2></div><button class="icon-btn" id="closeSkillImport" aria-label="关闭批量导入">${icon('x')}</button></div><div class="reader-body"><p class="subtle">支持多个 Markdown / .skill 文件，或包含多个技能的 ZIP。默认保留已安装的同名技能，其他项目继续导入。</p><div class="import-file-list">${files.map(f => `<div>${icon('file-archive')}<strong>${esc(f.name)}</strong><span>${(f.size / 1024).toFixed(1)} KB</span></div>`).join('')}</div><label class="checkbox-label"><input type="checkbox" id="updateFileSkills" />更新通过文件导入的同名本地技能</label><p class="subtle">已有本地修改会保留。来自本机目录或仓库的技能请用「检查更新」。</p></div><div class="dialog-actions"><button class="btn" id="cancelSkillImport">取消</button><button class="btn primary" id="confirmSkillImport">${icon('upload')}开始批量导入</button></div>`;
  $('#closeSkillImport').onclick = $('#cancelSkillImport').onclick = () => dialog.close();
  $('#confirmSkillImport').onclick = () => { const updateExisting = $('#updateFileSkills').checked; dialog.close(); action(() => runSkillBatch(files, 'file', {updateExisting})); };
  dialog.showModal(); icons();
}
function openTextReader(title, text) {
  const dialog = $('#workspaceDialog');
  dialog.innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">SKILL.MD</span><h2 id="workspaceTitle">${esc(title)}</h2></div><button class="icon-btn" id="closeWorkspace" aria-label="关闭内容">${icon('x')}</button></div><div class="reader-tabs segmented" role="group" aria-label="内容显示"><button class="active" data-reader-tab="preview">预览</button><button data-reader-tab="source">源码</button></div><div class="reader-body"><div id="workspacePreview" class="markdown">${markdown(text)}</div><pre id="workspaceText" hidden></pre></div><div class="dialog-actions"><button class="btn" id="exportSkill">${icon('download')}导出 Markdown</button></div>`;
  $('#workspaceText').textContent = text; $('#closeWorkspace').onclick = () => dialog.close();
  document.querySelectorAll('[data-reader-tab]').forEach(b => b.onclick = () => { document.querySelectorAll('[data-reader-tab]').forEach(v => v.classList.toggle('active', v === b)); $('#workspacePreview').hidden = b.dataset.readerTab !== 'preview'; $('#workspaceText').hidden = b.dataset.readerTab !== 'source'; });
  $('#exportSkill').onclick = () => download(`${title.replace(/[^\w\u4e00-\u9fff-]/g, '_')}.md`, text, 'text/markdown');
  bindMarkdown(dialog); dialog.showModal(); icons();
}

function openSkillRepos() {
  const dialog = $('#workspaceDialog');
  dialog.innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">DISCOVERY SOURCES</span><h2 id="workspaceTitle">Skills 仓库管理</h2></div><button class="icon-btn" id="closeWorkspace" aria-label="关闭仓库管理">${icon('x')}</button></div><div class="reader-body"><p class="subtle">添加公开 GitHub 仓库后，下载并缓存其中的 Skills。安装时会一并导入 references 和 scripts 文件。</p><form id="repoForm"><div class="form-grid"><div class="field"><label for="repoUrl">GitHub 仓库</label><input id="repoUrl" placeholder="anthropics/skills" required /></div><div class="field"><label for="repoBranch">分支</label><input id="repoBranch" value="main" required /></div></div><p class="err" id="repoError" role="alert" hidden></p><div class="dialog-actions"><button class="btn primary" ${!native() ? 'disabled' : ''}>${icon('plus')}添加并同步</button></div></form><div class="section-heading"><h2>已添加仓库</h2><span class="subtle">${skillsState.repos.length} 个</span></div>${skillsState.repos.map((r, i) => `<div class="repo-row"><div><strong>${esc(r.repo)}</strong><p class="subtle">${esc(r.branch)}</p></div><div class="actions"><button class="btn" data-sync-repo="${i}">同步</button><button class="icon-btn danger" data-remove-repo="${i}" aria-label="移除 ${esc(r.repo)}">${icon('trash-2')}</button></div></div>`).join('') || '<p class="subtle">还没有添加仓库。本机 Skills 会自动出现在发现页。</p>'}</div>`;
  $('#closeWorkspace').onclick = () => dialog.close();
  $('#repoForm').onsubmit = e => { e.preventDefault(); action(async () => {
    const form = e.target; form.inert = true;
    try {
      notice('正在下载仓库并扫描 Skills…');
      await workspaceCall('repo_add', { repo: $('#repoUrl').value.trim(), branch: $('#repoBranch').value.trim() });
      await loadSkills(); skillsState.tab = 'library'; renderSkills(); dialog.close(); notice('仓库已同步，选择 Skill 即可安装。');
    } catch (err) { $('#repoError').hidden = false; $('#repoError').textContent = String(err.message || err); }
    finally { form.inert = false; }
  }); };
  document.querySelectorAll('[data-sync-repo], [data-remove-repo]').forEach(b => b.onclick = () => action(async () => {
    const sync = b.hasAttribute('data-sync-repo'), r = skillsState.repos[Number(sync ? b.dataset.syncRepo : b.dataset.removeRepo)];
    b.disabled = true;
    try {
      await workspaceCall(sync ? 'repo_add' : 'repo_remove', sync ? { repo: r.repo, branch: r.branch } : { key: r.key });
      await loadSkills(); renderSkills(); dialog.close(); openSkillRepos(); notice(sync ? '仓库已同步。' : '仓库已移除，已安装技能保留。');
    } finally { b.disabled = false; }
  })); dialog.showModal(); icons();
}
