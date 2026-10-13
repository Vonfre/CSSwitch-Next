// Science owns installed skills; CSSwitch discovers and packages local/repository skills.
const skillsState = { tab: 'installed', search: '', source: 'all', installed: [], discovered: [], repos: [], error: '', degraded: false, localImportAvailable: false, customAllowed: true, warnings: [] };
const skillSources = { anthropic: 'Science 内置', bundled: 'Science 内置', personal: '个人 Skill', organization: '组织 Skill', imported: '已导入', 'csswitch-local': 'Science 本地', draft: '本地草稿', local: '本地 Skill', codex: 'Codex 本地', agents: '共享本地', claude: 'Claude 本地', github: 'GitHub 仓库' };
const workspaceCall = (operation, params = {}) => call('workspace_manage', { operation, params });

function skillsImportBlocked() { return !native() || skillsState.error || !skillsState.customAllowed || (skillsState.degraded && !skillsState.localImportAvailable); }

async function loadSkills() {
  skillsState.error = ''; skillsState.degraded = false; skillsState.localImportAvailable = false; skillsState.customAllowed = true; skillsState.warnings = []; skillsState.installed = []; skillsState.discovered = []; skillsState.repos = [];
  if (!native()) { skillsState.error = '请在桌面应用中管理 Skills。'; return; }
  const [catalog, discovery] = await Promise.allSettled([mcpCall('skills_list'), workspaceCall('skills_discover')]);
  if (catalog.status === 'fulfilled') {
    skillsState.installed = (catalog.value.skills || []).filter(s => !s.userHidden);
    skillsState.degraded = Boolean(catalog.value.degraded);
    skillsState.localImportAvailable = Boolean(catalog.value.localImportAvailable);
    skillsState.customAllowed = catalog.value.customAllowed !== false;
    if (skillsState.degraded) skillsState.warnings.push(skillsState.localImportAvailable ? 'Science 云端目录暂不可用，本地 Skills 仍可安装并绑定到 OPERON。云端技能的导入与卸载需要恢复官方登录。' : (catalog.value.localImportReason || 'Science 目录暂不可用，当前无法导入；请启动第三方 Science 或升级应用后刷新。'));
    if (!skillsState.customAllowed) skillsState.warnings.push('Science 组织策略禁止添加自定义 Skills。');
  } else skillsState.error = String(catalog.reason.message || catalog.reason);
  if (discovery.status === 'fulfilled') {
    skillsState.discovered = discovery.value.skills || []; skillsState.repos = discovery.value.repos || [];
    skillsState.warnings.push(...(discovery.value.warnings || []));
  } else skillsState.warnings.push(String(discovery.reason.message || discovery.reason));
}
function renderSkills() {
  const installed = skillsState.tab === 'installed';
  const rows = installed ? skillsState.installed : skillsState.discovered;
  const sources = [...new Set(rows.map(s => s.source))];
  $('#content').innerHTML = `<div class="page-intro"><div><h2>让 Science 学会更多工作方式</h2><p>管理已安装技能，或从本机与 GitHub 仓库发现新的 Skills。</p></div><div class="actions"><button class="btn" id="skillRepos">${icon('git-branch')}仓库管理</button><button class="btn primary" id="importSkill" ${skillsImportBlocked() ? 'disabled' : ''}>${icon('upload')}导入 Skill</button><input id="skillFile" type="file" accept=".zip,.skill,.md" hidden /></div></div>
    ${skillsState.error ? `<div class="mcp-note">${icon('info')} ${esc(skillsState.error)} · 已安装技能需要启动第三方 Science；本地发现仍可使用。</div>` : ''}
    ${skillsState.warnings.map(w => `<div class="mcp-note">${esc(w)}</div>`).join('')}
    <div class="management-toolbar"><div class="segmented" role="group" aria-label="Skills 视图"><button data-skill-tab="installed" class="${installed ? 'active' : ''}">已安装 <span class="label-count">${skillsState.installed.length}</span></button><button data-skill-tab="discover" class="${!installed ? 'active' : ''}">发现 <span class="label-count">${skillsState.discovered.length}</span></button></div><span class="subtle">安装到 Claude Science</span></div>
    <div class="filter-toolbar"><div class="search">${icon('search')}<input id="skillSearch" type="search" placeholder="搜索技能名称、描述" aria-label="搜索 Skills" value="${esc(skillsState.search)}" /></div><select id="skillSource" aria-label="Skills 来源"><option value="all">全部来源</option>${sources.map(s => `<option value="${esc(s)}" ${skillsState.source === s ? 'selected' : ''}>${esc(skillSources[s] || s)}</option>`).join('')}</select></div><div id="skillRows" class="management-list"></div>`;
  document.querySelectorAll('[data-skill-tab]').forEach(b => b.onclick = () => { skillsState.tab = b.dataset.skillTab; skillsState.source = 'all'; renderSkills(); });
  $('#skillSearch').oninput = e => { skillsState.search = e.target.value; renderSkillRows(); };
  $('#skillSource').onchange = e => { skillsState.source = e.target.value; renderSkillRows(); };
  $('#skillRepos').onclick = openSkillRepos;
  $('#importSkill').onclick = () => $('#skillFile').click();
  $('#skillFile').onchange = e => action(async () => {
    const file = e.target.files[0]; if (!file) return;
    try {
      if (file.size > 4 * 1024 * 1024) throw new Error('Skill 文件不能超过 4 MB。');
      const encoded = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error('无法读取 Skill 文件。')); reader.readAsDataURL(file); });
      const result = await mcpCall('skills_import', { filename: file.name, bundle: encoded });
      notice(result.message); await loadSkills(); skillsState.tab = 'installed'; renderSkills();
    } finally { e.target.value = ''; }
  });
  renderSkillRows(); icons();
}
function renderSkillRows() {
  const installed = skillsState.tab === 'installed';
  const q = skillsState.search.trim().toLowerCase();
  const rows = (installed ? skillsState.installed : skillsState.discovered).filter(s => (skillsState.source === 'all' || s.source === skillsState.source) && `${s.name} ${s.description || ''} ${s.repo || ''}`.toLowerCase().includes(q));
  $('#skillRows').innerHTML = rows.map((s, i) => {
    const exists = skillsState.installed.some(v => v.name === s.name || v.name === s.directory?.split('/').pop());
    return `<article class="management-row" data-skill-index="${i}"><span class="tile-icon">${icon('book-open')}</span><div class="row-detail"><h3>${esc(s.displayName || s.name)} <span class="badge">${esc(skillSources[s.source] || s.source)}</span></h3><p>${esc(s.description || '暂无描述')}</p><span class="subtle">${installed ? esc(s.admin_blocked ? '组织策略禁用' : s.enabled === false ? '已停用' : '已启用') : esc(s.repo || s.path || s.source)}${s.attachedAgents?.length ? ` · ${esc(s.attachedAgents.join(', '))}` : ''}</span></div><div class="actions">${installed ? `<button class="btn" data-skill-action="content">查看内容</button><button class="switch" role="switch" aria-label="启用 ${esc(s.name)}" aria-checked="${s.enabled !== false}" data-skill-action="enabled" ${s.admin_blocked ? 'disabled' : ''}><span></span></button>${['csswitch-local', 'local', 'draft', 'personal', 'imported', 'custom'].includes(s.source) ? `<button class="icon-btn danger" data-skill-action="remove" ${(s.source !== 'csswitch-local' && skillsState.degraded) || s.admin_blocked || !skillsState.customAllowed ? 'disabled' : ''} aria-label="卸载 ${esc(s.name)}">${icon('trash-2')}</button>` : ''}` : `<button class="btn ${exists ? '' : 'primary'}" data-skill-action="install" ${exists || skillsImportBlocked() ? 'disabled' : ''}>${icon(exists ? 'check' : 'download')}${exists ? '已安装' : '安装到 Science'}</button>`}</div></article>`;
  }).join('') || `<div class="empty">${icon('book-open')}<strong>${q ? '没有匹配的 Skills' : installed ? '暂无可用技能' : '没有发现新技能'}</strong><span>${installed ? '启动 Science 后刷新，或导入 ZIP / SKILL.md。' : '扫描 ~/.codex/skills、~/.agents/skills、~/.claude/skills；也可在仓库管理中添加 GitHub 仓库。'}</span></div>`;
  document.querySelectorAll('[data-skill-action]').forEach(b => b.onclick = () => action(async () => {
    const s = rows[Number(b.closest('[data-skill-index]').dataset.skillIndex)], op = b.dataset.skillAction;
    if (op === 'content') { const value = await mcpCall('skills_content', { name: s.name }); openTextReader(s.displayName || s.name, value.content || '此技能暂无可读取的内容。'); return; }
    if (op === 'remove' && !await confirmAction(`卸载 ${s.name}？`, 'Science 将移除技能及其代理绑定，原始本地技能和 GitHub 仓库保留。')) return;
    const result = op === 'install' ? await mcpCall('skills_import', await workspaceCall('skill_bundle', { key: s.key })) : await mcpCall(`skills_${op}`, { name: s.name, ...(op === 'enabled' ? { enabled: s.enabled === false } : {}) });
    notice(result.message); await loadSkills(); renderSkills();
  })); icons();
}
function openTextReader(title, text) {
  const dialog = $('#workspaceDialog');
  dialog.innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">SKILL.MD</span><h2 id="workspaceTitle">${esc(title)}</h2></div><button class="icon-btn" id="closeWorkspace" aria-label="关闭内容">${icon('x')}</button></div><div class="reader-body"><pre id="workspaceText"></pre></div><div class="dialog-actions"><button class="btn" id="exportSkill">${icon('download')}导出 Markdown</button></div>`;
  $('#workspaceText').textContent = text; $('#closeWorkspace').onclick = () => dialog.close();
  $('#exportSkill').onclick = () => download(`${title.replace(/[^\w\u4e00-\u9fff-]/g, '_')}.md`, text, 'text/markdown');
  dialog.showModal(); icons();
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
      await loadSkills(); skillsState.tab = 'discover'; renderSkills(); dialog.close(); notice('仓库已同步，选择 Skill 即可安装。');
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
