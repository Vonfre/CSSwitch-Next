const modelLabels = { model: '默认模型', quality_model: '质量模型', fast_model: '快速模型' };

function renderList() {
  const node = $('#connectionItems');
  if (!node) return;
  const items = state.config.connections.filter((p) => {
    const found = `${p.name} ${p.model} ${p.base_url} ${protocolNames[p.protocol]}`.toLowerCase().includes(state.search.toLowerCase());
    return found && (state.filter === 'all' || state.filter === 'ready' && p.has_key || state.filter === 'pending' && !p.has_key || state.filter === 'failed' && state.verified[p.id]?.ok === false);
  }).sort((a, b) => state.sort === 'active' && (a.id === state.config.provider || b.id === state.config.provider)
    ? Number(b.id === state.config.provider) - Number(a.id === state.config.provider) : a.name.localeCompare(b.name, 'zh-CN'));
  $('#resultCount').textContent = `${items.length} / ${state.config.connections.length}`;
  node.innerHTML = items.map((p) => {
    const current = p.id === state.config.provider;
    const v = state.verified[p.id];
    const testing = state.testing === p.id;
    return `<article class="connection-row ${current ? 'selected' : ''}">
      <button class="provider-main" data-edit="${esc(p.id)}" aria-label="编辑 ${esc(p.name)}">${mark(p)}<span class="connection-meta"><span class="connection-name">${esc(p.name)}${current ? '<span class="current-tag">当前</span>' : ''}</span><span class="connection-host">${esc(host(p.base_url))}</span></span></button>
      <div class="provider-model"><span title="${esc(p.model)}">${esc(p.model || '未选择模型')}</span><span class="connection-protocol">${esc(protocolNames[p.protocol])}</span></div>
      <div class="provider-status ${v ? v.ok ? 'ok' : 'err' : ''}"><span><span class="dot ${v ? v.ok ? 'green' : 'red' : p.has_key ? '' : 'amber'}"></span>${testing ? '测试中' : v ? v.ok ? '测试通过' : '测试失败' : p.has_key ? '未测试' : '待配置'}</span><small>${testing ? '最小请求' : v ? v.latency_ms != null ? `${fmt(v.latency_ms)} ms` : '检查配置' : p.has_key ? '密钥已保存' : '缺少密钥'}</small></div>
      <div class="provider-actions"><button class="icon-btn ${current ? 'current-action' : 'outlined'}" data-use="${esc(p.id)}" title="${current ? '当前提供商' : '切换到 ' + esc(p.name)}" aria-label="${current ? '当前提供商 ' : '切换到 '}${esc(p.name)}" ${current || !p.has_key ? 'disabled' : ''}>${icon(current ? 'check' : 'arrow-right-left')}</button><button class="icon-btn" data-test="${esc(p.id)}" title="测试 ${esc(p.name)}" aria-label="测试 ${esc(p.name)}" ${p.has_key ? '' : 'disabled'}>${icon('activity')}</button><button class="icon-btn" data-edit="${esc(p.id)}" title="编辑 ${esc(p.name)}" aria-label="配置 ${esc(p.name)}">${icon('pencil')}</button><details class="row-menu"><summary title="更多操作 ${esc(p.name)}" aria-label="更多操作 ${esc(p.name)}">${icon('ellipsis')}</summary><div class="dropdown"><button data-copy="${esc(p.id)}">${icon('copy')}复制配置</button><button class="danger" data-delete="${esc(p.id)}" ${current && state.runtime.proxy === 'green' ? 'disabled' : ''}>${icon('trash-2')}删除连接</button></div></details></div>
    </article>`;
  }).join('') || (state.config.connections.length ? `<div class="empty">${icon('search-x')}没有匹配的提供商<button class="btn" id="resetFilter">清除筛选</button></div>` : `<div class="empty">${icon('plug')}暂无提供商</div>`);
  node.querySelectorAll('[data-edit]').forEach((b) => b.onclick = () => openEditor(b.dataset.edit));
  node.querySelectorAll('[data-use]').forEach((b) => b.onclick = () => action(() => useConnection(b.dataset.use)));
  node.querySelectorAll('[data-test]').forEach((b) => b.onclick = () => testSaved(b.dataset.test));
  node.querySelectorAll('[data-delete]').forEach((b) => b.onclick = () => deleteConnection(b.dataset.delete));
  node.querySelectorAll('[data-copy]').forEach((b) => b.onclick = () => {
    const p = state.config.connections.find((p) => p.id === b.dataset.copy);
    newDraft({ ...p, name: `${p.name} 副本`, has_key: false, key_masked: '', builtin: false });
  });
  node.querySelectorAll('.row-menu').forEach((menu) => menu.addEventListener('toggle', () => {
    if (menu.open) node.querySelectorAll('.row-menu').forEach((other) => { if (other !== menu) other.open = false; });
  }));
  if ($('#resetFilter')) $('#resetFilter').onclick = () => { state.search = ''; state.filter = 'all'; $('#search').value = ''; $('#providerFilter').value = 'all'; renderList(); };
  icons();
}

async function useConnection(id) {
  await call('select_connection', { id }); await loadConfig(); renderRuntime(); renderList();
  notice(`已切换到 ${active().name}${state.runtime.proxy === 'green' ? '，后续请求使用新连接' : ''}。`);
}

async function deleteConnection(id) {
  const p = state.config.connections.find((p) => p.id === id);
  if (!await confirmAction('删除这个连接？', `${p.name} 的配置和密钥将被删除。Science 项目不受影响。`)) return;
  await action(async () => { await call('delete_connection', { id }); delete state.verified[id]; delete state.catalogs[id]; await loadConfig(); renderRuntime(); renderList(); notice('连接已删除。'); });
}

async function testSaved(id) {
  const p = state.config.connections.find((p) => p.id === id);
  if (!await confirmAction('测试模型调用？', `将向 ${host(p.base_url)} 的 ${p.model} 发送最小测试请求，可能产生少量费用。不会发送研究对话，也不会切换当前提供商。`)) return;
  await action(async () => {
    notice(`正在测试 ${p.name}...`);
    state.testing = id; renderList();
    try {
      const result = await call('verify_connection', { id });
      state.verified[id] = result;
      notice(result.ok ? `${p.name} 测试通过，${fmt(result.latency_ms)} ms。` : result.hint, !result.ok);
    } finally { state.testing = null; renderList(); renderRuntime(); }
  });
}

function openEditor(id) {
  if (state.busy) return;
  state.selected = id; state.draft = null; state.dirty = false; state.draftTest = null; state.editorTab = 'connection';
  renderEditor(); $('#providerDialog').showModal();
}

function newDraft(profile) {
  state.selected = `api-${crypto.randomUUID()}`;
  state.draft = { ...profile, id: state.selected, quality_model: profile.quality_model || '', fast_model: profile.fast_model || '', max_tokens: profile.max_tokens || 8192, has_key: false };
  state.preset = 'custom'; state.dirty = false; state.draftTest = null; state.editorTab = 'connection';
  renderEditor(); if (!$('#providerDialog').open) $('#providerDialog').showModal();
}

function openPresets() {
  $('#editor').innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">NEW PROVIDER</span><h2 id="providerTitle">添加提供商</h2></div><button class="icon-btn" id="closeEditor" aria-label="关闭配置" title="关闭配置">${icon('x')}</button></div><div class="preset-body"><div class="search">${icon('search')}<input type="search" id="presetSearch" aria-label="搜索服务商" placeholder="搜索服务商或地址" /></div><div class="preset-grid" id="presetGrid"></div></div>`;
  const draw = () => {
    const query = $('#presetSearch').value.trim().toLowerCase();
    $('#presetGrid').innerHTML = Object.entries(templates).filter(([id, p]) => `${p.name || '自定义 API'} ${p.base_url} ${id}`.toLowerCase().includes(query)).map(([id, p]) => `<button class="preset-item" data-preset="${id}"><span class="provider-mark ${p.protocol}">${icon(templateIcons[id])}</span><span><strong>${esc(p.name || '自定义 API')}</strong><small>${esc(id === 'custom' ? '自定义地址与协议' : host(p.base_url))}</small></span>${icon('chevron-right')}</button>`).join('') || '<div class="empty">没有匹配的服务商</div>';
    document.querySelectorAll('[data-preset]').forEach((b) => b.onclick = () => { newDraft(templates[b.dataset.preset]); state.preset = b.dataset.preset; $('#presetName').textContent = templates[state.preset].name || '自定义 API'; });
    icons();
  };
  $('#presetSearch').oninput = draw; $('#closeEditor').onclick = closeEditor; draw();
  if (!$('#providerDialog').open) $('#providerDialog').showModal();
}

async function closeEditor() {
  if (state.busy) return;
  if (state.dirty && !await confirmAction('放弃未保存的修改？', '关闭配置会丢弃当前修改，已保存的连接不受影响。')) return;
  $('#providerDialog').close(); state.catalogRequest++; state.catalogBusy = false;
  state.draft = null; state.dirty = false; state.draftTest = null; state.selected = state.config.provider;
  $('#editor').innerHTML = '';
}

function setEditorTab(tab) {
  state.editorTab = tab;
  document.querySelectorAll('[data-editor-tab]').forEach((b) => { b.classList.toggle('active', b.dataset.editorTab === tab); b.setAttribute('aria-selected', b.dataset.editorTab === tab); b.tabIndex = b.dataset.editorTab === tab ? 0 : -1; });
  document.querySelectorAll('[data-pane]').forEach((pane) => pane.hidden = pane.dataset.pane !== tab);
}

function validEditor() {
  const invalid = $('#connectionForm').querySelector(':invalid');
  if (invalid) setEditorTab(invalid.closest('[data-pane]')?.dataset.pane || 'connection');
  return $('#connectionForm').reportValidity();
}

function renderEditor() {
  const p = selected(); if (!p) return;
  const isNew = Boolean(state.draft);
  $('#editor').innerHTML = `<div class="dialog-heading"><div><span class="eyebrow">${isNew ? 'NEW PROVIDER' : 'PROVIDER SETTINGS'}</span><h2 id="providerTitle">${isNew ? '配置提供商' : esc(p.name)}</h2></div><div class="actions"><span class="badge" id="draftBadge" hidden>未保存</span><button class="icon-btn" id="closeEditor" title="关闭配置" aria-label="关闭配置">${icon('x')}</button></div></div>
    <div id="editorNotice" class="notice error" role="status" hidden></div>
    ${isNew ? `<div class="preset-bar">${icon('blocks')}<span id="presetName">${esc(templates[state.preset]?.name || '自定义 API')}</span><button type="button" class="icon-btn" id="changePreset" title="更换服务商模板" aria-label="更换服务商模板">${icon('arrow-right-left')}</button></div>` : ''}
    <div class="editor-tabs" role="tablist" aria-label="提供商配置"><button type="button" role="tab" id="tab-connection" aria-controls="pane-connection" data-editor-tab="connection">${icon('plug')}连接</button><button type="button" role="tab" id="tab-models" aria-controls="pane-models" data-editor-tab="models">${icon('cpu')}模型</button><button type="button" role="tab" id="tab-test" aria-controls="pane-test" data-editor-tab="test">${icon('activity')}测试</button></div>
    <form id="connectionForm"><div class="editor-body">
      <section id="pane-connection" data-pane="connection" role="tabpanel" aria-labelledby="tab-connection"><div class="form-section-heading"><h3>连接信息</h3><span class="subtle">${esc(protocolNames[p.protocol])}</span></div><div class="form-grid"><div class="field"><label for="name">连接名称</label><input id="name" name="name" value="${esc(p.name)}" required maxlength="80" placeholder="我的 API" /></div><div class="field"><label for="protocol">API 协议</label><select id="protocol" name="protocol">${Object.entries(protocolNames).map(([v, n]) => `<option value="${v}" ${v === p.protocol ? 'selected' : ''}>${n}</option>`).join('')}</select></div><div class="field wide"><label for="base_url">API 地址</label><input type="url" id="base_url" name="base_url" value="${esc(p.base_url)}" required placeholder="https://api.example.com/v1" spellcheck="false" /></div><div class="field wide"><div class="model-heading"><label for="key">API Key</label>${p.has_key ? '<span class="key-state">'+icon('shield-check')+'已保存</span>' : ''}</div><div class="secret-input"><input id="key" name="key" type="password" placeholder="${p.has_key ? '保留已保存的密钥' : '填写 API Key'}" autocomplete="off" spellcheck="false" /><button type="button" class="icon-btn" id="showKeyBtn" title="显示或隐藏输入的密钥" aria-label="显示或隐藏输入的密钥">${icon('eye')}</button></div></div></div></section>
      <section id="pane-models" data-pane="models" role="tabpanel" aria-labelledby="tab-models"><div class="form-section-heading"><h3>模型路由 ${help('自动模型映射', '自动读取本机 Science 的公开模型标识。Sonnet 调用默认模型，Opus 调用质量模型，Haiku 调用快速模型；质量和快速留空时继承默认。Science 升级后按系列匹配新版本，旧会话仍可使用。保存后下一请求使用新映射，刷新 Science 页面更新菜单。')}</h3><button type="button" class="btn" id="fetchModelsBtn" title="获取模型列表" aria-label="获取模型列表">${icon('refresh-cw')}获取模型</button></div><div id="catalogStatus" class="catalog-status" role="status"></div><div class="model-routing">${Object.entries(modelLabels).map(([id, label], i) => `<div class="route-field"><div><label for="${id}">${label}</label><span class="route-alias">${['Sonnet', 'Opus', 'Haiku'][i]}</span></div>${modelControl(id, p[id], id === 'model')}</div>`).join('')}</div><div class="field token-field"><label for="max_tokens">最大输出 Tokens</label><input type="number" id="max_tokens" name="max_tokens" min="1" max="1000000" value="${p.max_tokens || 8192}" required /></div></section>
      <section id="pane-test" data-pane="test" role="tabpanel" aria-labelledby="tab-test"><div class="form-section-heading"><h3>模型调用测试</h3><span class="badge">最小请求</span></div><div class="test-target"><span>测试地址</span><code id="testEndpoint"></code><span>测试模型</span><code id="testModel"></code></div><div class="verification" id="verification" role="status"></div><div class="test-footer"><button type="button" class="btn" id="verifyBtn">${icon('activity')}测试连接</button><span class="subtle">可能产生少量 API 费用</span></div></section>
    </div><div class="editor-actions"><div class="actions">${!isNew ? `<button type="button" class="icon-btn danger" id="clearKeyBtn" title="清除密钥" aria-label="清除密钥" ${p.has_key ? '' : 'disabled'}>${icon('key-round')}</button>` : ''}<button type="button" class="btn" id="cancelEditor">取消</button></div><div class="actions"><button type="button" class="btn" id="saveUseBtn">${icon('arrow-right-left')}保存并使用</button><button class="btn primary" type="submit">${icon('save')}保存连接</button></div></div></form>`;
  $('#closeEditor').onclick = closeEditor; $('#cancelEditor').onclick = closeEditor;
  $('#connectionForm').noValidate = true;
  $('#connectionForm').onsubmit = (e) => { e.preventDefault(); action(() => saveEditor()); };
  $('#saveUseBtn').onclick = () => action(() => saveEditor(true));
  $('#showKeyBtn').onclick = () => { const show = $('#key').type === 'password'; $('#key').type = show ? 'text' : 'password'; $('#showKeyBtn').innerHTML = icon(show ? 'eye-off' : 'eye'); icons(); };
  $('#connectionForm').oninput = (e) => {
    state.dirty = true; state.draftTest = null; $('#draftBadge').hidden = false;
    if (['base_url', 'protocol', 'key'].includes(e.target.id)) invalidateCatalog();
    showVerification(); updateTestTarget();
  };
  document.querySelectorAll('[data-editor-tab]').forEach((b) => {
    b.onclick = () => setEditorTab(b.dataset.editorTab);
    b.onkeydown = (e) => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return; e.preventDefault(); const tabs = [...document.querySelectorAll('[data-editor-tab]')]; const idx = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (tabs.indexOf(b) + (e.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length; tabs[idx].click(); tabs[idx].focus(); };
  });
  if ($('#changePreset')) $('#changePreset').onclick = async () => { if (state.dirty && !await confirmAction('更换服务商模板？', '当前未保存的配置将被重置。')) return; state.dirty = false; state.catalogRequest++; state.catalogBusy = false; openPresets(); };
  if ($('#clearKeyBtn')) $('#clearKeyBtn').onclick = async () => {
    if (state.dirty) { notice('请先保存或取消当前修改。', true); return; }
    if (!await confirmAction('清除密钥？', '此连接需要重新填写 API Key 才能使用。')) return;
    action(async () => { await call('clear_connection_key', { id: p.id }); delete state.verified[p.id]; delete state.catalogs[p.id]; await loadConfig(); renderEditor(); renderList(); notice('密钥已清除。'); });
  };
  $('#verifyBtn').onclick = () => action(testDraft);
  $('#fetchModelsBtn').onclick = () => fetchModels(); bindModelControls();
  setEditorTab(state.editorTab); updateTestTarget(); showVerification();
  if (state.catalogs[p.id]?.signature === catalogSignature()) applyCatalog();
  else if (native() && p.has_key && !state.catalogAttempts.has(p.id + catalogSignature())) {
    queueMicrotask(() => { if (state.selected === p.id && !state.dirty && $('#connectionForm')) fetchModels(); });
  }
  icons();
}

function updateTestTarget() {
  $('#testEndpoint').textContent = $('#base_url').value || '未填写'; $('#testModel').textContent = $('#model').value || '未选择';
}

function showVerification() {
  const node = $('#verification'); if (!node) return;
  const v = state.draftTest?.result || (!state.dirty && state.verified[state.selected]);
  const testing = state.testing === state.selected;
  node.className = `verification ${v ? v.ok ? 'ok' : 'err' : ''}`;
  node.innerHTML = testing ? `${icon('loader-circle')}<strong>正在测试模型调用...</strong>` : v ? `${icon(v.ok ? 'circle-check' : 'circle-x')}<div><strong>${v.ok ? '测试通过' : '测试失败'}${v.latency_ms != null ? `<span>${fmt(v.latency_ms)} ms</span>` : ''}</strong><span>${esc(v.hint)}</span></div>` : `${icon('circle-dashed')}<span>${state.dirty ? '当前草稿尚未测试' : '尚未测试此连接'}</span>`;
  $('#verifyBtn').disabled = testing; icons();
}

async function testDraft() {
  if (!validEditor()) return;
  const input = readDraft(); const id = state.selected;
  if (!input.key && !selected().has_key) { setEditorTab('connection'); $('#key').focus(); notice('请填写 API Key。', true); return; }
  state.testing = id; showVerification();
  try {
    const result = await call('test_connection', { input });
    state.draftTest = { signature: testSignature(input), result };
    if (!state.dirty) { state.verified[id] = result; renderList(); renderRuntime(); }
  } finally { state.testing = null; showVerification(); }
}

async function saveEditor(use = false) {
  if (!validEditor()) return;
  const input = readDraft(); const verdict = state.draftTest?.signature === testSignature(input) ? state.draftTest.result : !state.dirty ? state.verified[input.id] : null;
  await call('save_connection', { input });
  state.dirty = false; state.draft = null;
  if (verdict) state.verified[input.id] = verdict; else delete state.verified[input.id];
  await loadConfig();
  // A saved configuration must remain saved even if activation subsequently fails.
  if (use) {
    try { await call('select_connection', { id: input.id }); await loadConfig(); }
    catch (e) { renderEditor(); renderList(); throw new Error(`连接已保存，但未切换：${e.message || e}`); }
  }
  $('#providerDialog').close(); $('#editor').innerHTML = ''; state.catalogRequest++; state.catalogBusy = false;
  renderRuntime(); renderList(); notice((use ? `已保存并使用 ${input.name}。` : '连接已保存。') + (state.runtime.proxy === 'green' && state.config.provider === input.id ? ' 模型映射对后续请求生效，刷新 Science 页面可更新菜单。' : ''));
}

function modelControl(id, value, required) {
  return `<div class="model-control"><input id="${id}" name="${id}" value="${esc(value)}" placeholder="${required ? '选择或输入模型 ID' : '继承默认模型'}" spellcheck="false" ${required ? 'required' : ''} /><button type="button" class="icon-btn outlined" data-model="${id}" title="选择${modelLabels[id]}" aria-label="选择${modelLabels[id]}">${icon('list-filter')}</button></div>`;
}

function catalogSignature() { return `${$('#protocol')?.value}|${$('#base_url')?.value.trim().replace(/\/+$/, '')}`; }
function invalidateCatalog() {
  state.catalogRequest++; state.catalogBusy = false; delete state.catalogs[state.selected];
  $('#catalogStatus').textContent = ''; $('#catalogStatus').classList.remove('err'); $('#fetchModelsBtn').disabled = false;
  $('#fetchModelsBtn').classList.remove('loading');
}

function bindModelControls() {
  document.querySelectorAll('[data-model]').forEach((b) => b.onclick = () => {
    state.modelTarget = b.dataset.model; $('#modelTitle').textContent = `选择${modelLabels[state.modelTarget]}`;
    $('#modelSearch').value = ''; updateModelOptions(); $('#modelDialog').showModal(); $('#modelSearch').focus();
  });
}

function updateModelOptions() {
  if (!state.modelTarget || !$('#connectionForm')) return;
  const all = state.catalogs[state.selected]?.models || [];
  const query = $('#modelSearch').value.trim().toLowerCase(); const current = $('#' + state.modelTarget).value;
  const models = all.filter((m) => `${m.id} ${m.name}`.toLowerCase().includes(query));
  if (current && !all.some((m) => m.id === current) && `${current}`.toLowerCase().includes(query)) models.unshift({ id: current, name: current, custom: true });
  $('#modelCount').textContent = `${all.length} 个可用模型`;
  $('#pickerStatus').textContent = $('#catalogStatus').textContent;
  $('#pickerRefresh').disabled = state.catalogBusy; $('#pickerRefresh').classList.toggle('spin', state.catalogBusy);
  $('#modelOptions').innerHTML = (state.modelTarget !== 'model' && !query ? `<button type="button" role="option" aria-selected="${!current}" data-model-value=""><span>${icon('corner-down-right')}继承默认模型</span>${!current ? icon('check') : ''}</button>` : '') + models.map((m) => `<button type="button" role="option" aria-selected="${m.id === current}" data-model-value="${esc(m.id)}"><span><strong>${esc(m.name || m.id)}</strong>${m.name && m.name !== m.id ? `<small>${esc(m.id)}</small>` : ''}${m.custom ? '<small>自定义模型</small>' : ''}</span>${m.id === current ? icon('check') : ''}</button>`).join('') + (!models.length ? `<div class="empty">${icon('search-x')}${query ? '没有匹配的模型' : state.catalogBusy ? '正在获取模型...' : '尚无模型目录'}</div>` : '');
  $('#modelOptions').querySelectorAll('[data-model-value]').forEach((b) => b.onclick = () => {
    const field = $('#' + state.modelTarget); const changed = field.value !== b.dataset.modelValue;
    field.value = b.dataset.modelValue; if (changed) field.dispatchEvent(new Event('input', { bubbles: true }));
    $('#modelDialog').close(); field.focus();
  });
  icons();
}

function applyCatalog() {
  $('#catalogStatus').textContent = `${state.catalogs[state.selected].models.length} 个模型 · 已获取`;
  $('#catalogStatus').classList.remove('err');
  if ($('#modelDialog').open) updateModelOptions();
}

async function fetchModels() {
  if (state.catalogBusy || state.busy) return;
  const input = readDraft(); if (!input) return;
  if (!$('#base_url').checkValidity()) { setEditorTab('connection'); $('#base_url').reportValidity(); return; }
  const signature = catalogSignature(); const id = state.selected; const request = ++state.catalogRequest;
  state.catalogAttempts.add(id + signature); state.catalogBusy = true;
  $('#catalogStatus').textContent = '正在获取模型...'; $('#catalogStatus').classList.remove('err');
  $('#fetchModelsBtn').disabled = true; $('#fetchModelsBtn').classList.add('loading');
  if ($('#modelDialog').open) updateModelOptions();
  try {
    const result = await call('list_models', { query: { id, protocol: input.protocol, base_url: input.base_url, key: input.key } });
    if (request !== state.catalogRequest || state.selected !== id || !$('#connectionForm') || signature !== catalogSignature() || $('#key').value !== (input.key || '')) return;
    if (!result.ok) throw new Error(result.hint || '无法获取模型列表。');
    state.catalogs[id] = { signature, models: result.models }; applyCatalog();
  } catch (e) {
    if (request === state.catalogRequest && $('#catalogStatus')) { $('#catalogStatus').textContent = String(e.message || e); $('#catalogStatus').classList.add('err'); }
  } finally {
    if (request === state.catalogRequest) {
      state.catalogBusy = false; if ($('#fetchModelsBtn')) { $('#fetchModelsBtn').disabled = false; $('#fetchModelsBtn').classList.remove('loading'); }
      if ($('#modelDialog').open) updateModelOptions();
    }
  }
}

function renderWorkbench() {
  const official = state.config.mode === 'official';
  $('#content').innerHTML = `<div class="mode-line"><div class="segmented" role="group" aria-label="运行模式"><button data-mode="proxy" class="${official ? '' : 'active'}" aria-pressed="${!official}">${icon('network')}第三方模型</button><button data-mode="official" class="${official ? 'active' : ''}" aria-pressed="${official}">${icon('asterisk')}官方 Claude</button></div><span>${icon('folder-check')}共享本机对话</span></div>${official ? `<div class="official">${icon('flask-conical')}<h2>官方 Claude Science</h2><p>使用你自己的 Claude 账号与订阅登录。</p><button class="btn primary" id="officialBtn">${icon('external-link')}打开官方 Science</button></div>` : '<section id="runtimeSection"></section>'}
    <div class="section-heading"><h2>提供商 <span class="label-count">${state.config.connections.length}</span></h2><button class="btn primary" id="addBtn">${icon('plus')}添加提供商</button></div><div class="provider-toolbar"><div class="search">${icon('search')}<input id="search" type="search" aria-label="搜索连接" placeholder="搜索提供商、模型或地址" value="${esc(state.search)}" /></div><select id="providerFilter" aria-label="筛选提供商"><option value="all">全部状态</option><option value="ready">已配置</option><option value="pending">待配置</option><option value="failed">测试失败</option></select><select id="providerSort" aria-label="排序提供商"><option value="active">当前优先</option><option value="name">名称排序</option></select></div><div class="list-heading"><span>提供商 / 地址</span><span>模型 / 协议</span><span>测试状态</span><span id="resultCount"></span></div><div id="connectionItems" class="connection-items"></div>`;
  document.querySelectorAll('[data-mode]').forEach((b) => b.onclick = async () => {
    if (b.dataset.mode === state.config.mode) return;
    if (b.dataset.mode === 'official' && state.runtime.sandbox === 'green' && !await confirmAction('切换到官方模式？', '共享 Science 和网关会停止。对话记录保留，请先结束正在运行的任务。')) return;
    action(async () => { await call('set_mode', { mode: b.dataset.mode }); await loadConfig(); await refreshRuntime(); renderWorkbench(); notice('运行模式已切换。'); });
  });
  if (official) $('#officialBtn').onclick = () => action(async () => { await call('open_official'); notice('已打开官方 Claude Science。'); });
  else renderRuntime();
  $('#search').oninput = (e) => { state.search = e.target.value; renderList(); };
  $('#providerFilter').value = state.filter; $('#providerFilter').onchange = (e) => { state.filter = e.target.value; renderList(); };
  $('#providerSort').value = state.sort; $('#providerSort').onchange = (e) => { state.sort = e.target.value; renderList(); };
  $('#addBtn').onclick = () => { state.dirty = false; state.draft = null; openPresets(); };
  renderList(); icons();
}

document.addEventListener('DOMContentLoaded', () => {
  $('#providerDialog').addEventListener('cancel', (e) => { e.preventDefault(); closeEditor(); });
  $('#modelDialog').addEventListener('close', () => { state.modelTarget = null; });
  $('#modelDialog').addEventListener('keydown', (e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); $('#modelDialog').close(); } });
  $('#closeModels').onclick = () => $('#modelDialog').close();
  $('#pickerRefresh').onclick = fetchModels;
  $('#modelSearch').oninput = updateModelOptions;
  $('#modelSearch').onkeydown = (e) => { if (e.key === 'Enter') e.preventDefault(); if (e.key === 'ArrowDown') { e.preventDefault(); $('#modelOptions [role="option"]')?.focus(); } };
  $('#modelOptions').onkeydown = (e) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key)) return;
    e.preventDefault(); const options = [...$('#modelOptions').querySelectorAll('[role="option"]')];
    const index = e.key === 'Home' ? 0 : e.key === 'End' ? options.length - 1 : (options.indexOf(document.activeElement) + (e.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length;
    options[index]?.focus();
  };
  document.addEventListener('click', (e) => { document.querySelectorAll('.row-menu[open]').forEach((menu) => { if (!menu.contains(e.target)) menu.open = false; }); });
});
