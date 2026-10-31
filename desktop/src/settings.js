function renderSettings() {
  const d = state.diagnostics;
  const paths = d ? [['配置', d.config_dir], ['对话', d.science_data_dir], ['本地认证', d.science_auth_dir], ['保留的旧目录', d.legacy_sandbox_home], ['Python', d.python]] : [];
  $('#content').innerHTML = `<div class="settings-page">
    <section class="preference-group" aria-labelledby="generalHeading"><h2 id="generalHeading">通用</h2><div class="preference-panel">
      <div class="preference-row"><div class="preference-label"><label for="themePreference">外观</label><span>选择浅色、深色或跟随系统</span></div><select id="themePreference" aria-label="外观"><option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option></select></div>
      <div class="preference-row"><div class="preference-label"><div><label for="fontPreference">界面字体</label>${help('界面字体', '用于 CSSwitch 的导航、设置和阅读界面，即时生效并自动记住选择。代码、日志与路径保留等宽字体。未安装的字体会使用系统替代字体，Helvetica 的中文使用苹方或雅黑；不更改 Science 页面字体。')}</div><span>让文字读起来更舒适</span></div><select id="fontPreference" aria-label="界面字体">${uiFonts.map(font => `<option value="${font.id}">${font.label}</option>`).join('')}</select></div>
      <div class="preference-row"><div class="preference-label"><div>Science 页面汉化 ${help('页面汉化', '汉化通过 CSSwitch 打开的 Science 导航、菜单与设置。新文案先核对原版界面资源，再发送给所选翻译服务；聊天、文件和输入内容不会发送。Edge 翻译通道参考 MIT 开源项目 translate.js 接入，无需模型 Key，服务可用性由微软决定。AI 翻译使用当前连接的默认模型，消耗 Token。译文自动缓存；失败时保留原文，不会自动切换到收费模型。关闭汉化后恢复英文。')}</div><span>导航、菜单与设置显示中文</span></div><button class="switch" role="switch" id="scienceZhToggle" aria-label="Science 页面汉化" aria-checked="${Boolean(state.config.science_zh)}"><span></span></button></div>
      ${state.config.science_zh ? `<div class="preference-row"><div class="preference-label"><label for="scienceTranslationMode">翻译方式</label><span>${state.config.science_zh_online ? '新文案自动翻译，译文保存在本机' : '使用已有译文和本机缓存'}</span></div><select id="scienceTranslationMode" aria-label="翻译方式"><option value="local" ${!state.config.science_zh_online ? 'selected' : ''}>本地翻译</option><option value="edge" ${state.config.science_zh_online && state.config.science_zh_engine !== 'api' ? 'selected' : ''}>Edge 自动翻译</option><option value="api" ${state.config.science_zh_online && state.config.science_zh_engine === 'api' ? 'selected' : ''}>AI 自动翻译</option></select></div>` : ''}
      <div class="preference-row"><div class="preference-label"><div>本地文件打开 ${help('本地文件打开', '在通过 CSSwitch 打开的 Science 文件菜单中，直接用默认应用或文本编辑器打开当前版本，也可在 文件管理器中显示，无需重复下载。仅支持有本地存储记录的文件；编辑会更改实际文件，Science 内部版本的校验可能受影响。macOS 使用系统默认文本编辑器，Windows 使用记事本。首次更新后请重启 CSSwitch，再从提供商页打开 Science；官方客户端窗口不加载此插件。')}</div><span>生成文件直接打开，无需下载</span></div><button class="switch" role="switch" id="scienceFilesToggle" aria-label="本地文件打开" aria-checked="${Boolean(state.config.science_local_files)}"><span></span></button></div>
    </div></section>
    <section class="preference-group" aria-labelledby="environmentHeading"><h2 id="environmentHeading">应用与环境</h2><div class="preference-panel"><div id="updateCard"></div><div id="compatibilityCard">${compatibilityCard()}</div></div></section>
    <details class="settings-advanced" ${state.settingsAdvanced ? 'open' : ''}><summary><div><strong>高级设置</strong><span>服务端口与本地存储</span></div>${icon('chevron-right')}</summary>
      <div class="advanced-content"><div class="setting-heading"><h3>本地服务端口</h3>${help('本地服务端口', '修改端口前需要停止网关与 Science，正在运行的研究会中断；保存时会再次确认。网关不能使用 Science 保留端口 8765，Science 本身可以使用。')}</div>
      <form id="settingsForm"><div class="form-grid"><div class="field"><label for="proxyPort">网关</label><input id="proxyPort" name="proxy_port" type="number" min="1024" max="65535" value="${state.config.proxy_port}" required /></div><div class="field"><label for="sandboxPort">Science</label><input id="sandboxPort" name="sandbox_port" type="number" min="1024" max="65535" value="${state.config.sandbox_port}" required /></div></div><div class="actions"><button class="btn" type="submit">保存端口</button></div></form>
      <div class="setting-heading"><h3>本地存储</h3>${help('本地存储', 'API Key 以明文保存在仅当前用户可访问的本机配置文件（Windows ACL / macOS 0600）中。用量记录不保存对话内容。对话仍在 Science 原目录，CSSwitch 的本地认证独立保存。')}</div>
      ${d?.science_history ? `<p class="storage-summary">${fmt(d.science_history.projects)} 个项目 <span>·</span> ${fmt(d.science_history.conversations)} 个会话 <span>·</span> ${fmt(d.science_history.messages)} 条消息</p>` : ''}
      <dl class="environment-paths" id="environmentCard">${paths.filter(([, value]) => value).map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`).join('') || '<p class="subtle">请在桌面应用中检查本地环境。</p>'}</dl>
      </div></details>
    <p class="settings-footer">CSSwitch Next ${esc(d?.version || updateState.current || '')}${d?.platform ? ` <span>·</span> ${esc(d.platform)}` : ''}</p>
  </div>`;
  $('#themePreference').value = localStorage.getItem('csswitch-theme') || 'system';
  $('#themePreference').onchange = e => { localStorage.setItem('csswitch-theme', e.target.value); applyTheme(e.target.value); };
  applyFont();
  $('#fontPreference').onchange = e => { localStorage.setItem('csswitch-font', e.target.value); applyFont(e.target.value); };
  $('.settings-advanced').ontoggle = e => { if (e.target.isConnected) state.settingsAdvanced = e.target.open; };
  $('#scienceZhToggle').onclick = () => action(async () => {
    const zh = !state.config.science_zh;
    await call('set_science_features', { localMcp: state.config.science_local_mcp !== false, zh });
    await loadConfig(); renderSettings(); notice(zh ? '页面汉化已开启。' : '页面汉化已关闭。');
  });
  $('#scienceFilesToggle').onclick = () => action(async () => {
    const localFiles = !state.config.science_local_files;
    await call('set_science_features', { localMcp: state.config.science_local_mcp !== false, zh: Boolean(state.config.science_zh), localFiles });
    await loadConfig(); renderSettings(); notice(localFiles ? '本地文件打开已开启，请在 Science 文件菜单中使用。' : '本地文件打开已关闭。');
  });
  const translationMode = $('#scienceTranslationMode');
  if (translationMode) translationMode.onchange = e => action(async () => {
    const mode = e.target.value, onlineZh = mode !== 'local';
    await call('set_science_features', { localMcp: state.config.science_local_mcp !== false, zh: Boolean(state.config.science_zh), onlineZh, ...(onlineZh ? {translationEngine:mode} : {}) });
    await loadConfig(); renderSettings(); notice(onlineZh ? '联网自动翻译已开启，新译文将缓存到本机。' : '已切换到本地翻译。');
  });
  $('#settingsForm').onsubmit = e => { e.preventDefault(); action(async () => {
    const values = Object.fromEntries(new FormData(e.target));
    const proxy_port = Number(values.proxy_port), sandbox_port = Number(values.sandbox_port);
    if (proxy_port === sandbox_port || proxy_port === 8765) { notice('端口不能相同，网关不能使用 Science 保留端口 8765。', true); return; }
    if (proxy_port === state.config.proxy_port && sandbox_port === state.config.sandbox_port) { notice('端口未变更。'); return; }
    if (state.runtime.proxy === 'green' || state.runtime.sandbox === 'green') {
      if (!await confirmAction('停止服务并更改端口？', '正在运行的研究任务会中断，对话记录保留。')) return;
      await call('stop_all');
    }
    await call('set_config', { cfg: { provider: state.config.provider, proxy_port, sandbox_port } });
    await loadConfig(); await refreshRuntime(); renderSettings(); notice('端口已保存。');
  }); };
  bindCompatibility(); renderUpdater(); icons();
}
