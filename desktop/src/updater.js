const updateState = { current: '', available: false, downloaded: false, phase: 'idle', error: '', downloadedBytes: 0, total: null };

function renderUpdater() {
  const badge = $('#updateBadge');
  badge.hidden = !updateState.available;
  badge.textContent = `新版 ${updateState.version || ''}`;
  badge.onclick = () => showView('settings');
  const card = $('#updateCard');
  if (!card) return;
  const u = updateState;
  const busy = ['checking', 'downloading', 'installing'].includes(u.phase);
  const progress = u.total ? Math.min(100, Math.round(100 * u.downloadedBytes / u.total)) : null;
  const status = { idle: '启动时自动检查，也可以手动检查。', checking: '正在检查更新…',
    checked: '当前已是最新版本。', available: `发现新版本 ${u.version}`, downloading: `正在下载… ${progress === null ? `${(u.downloadedBytes / 1048576).toFixed(1)} MB` : `${progress}%`}`,
    downloaded: '更新包已下载，签名验证通过。', installing: '正在安装，即将重启…' }[u.phase];
  card.innerHTML = `<h2>应用更新 <span class="label-count">${esc(u.current || state.diagnostics?.version || '')}</span></h2>
    <p class="hint" role="status">${esc(status)}</p>${u.error ? `<p class="update-error" role="alert">${esc(u.error)}</p>` : ''}
    ${u.phase === 'downloading' ? `<progress aria-label="更新下载进度" ${progress === null ? '' : `value="${progress}" max="100"`}></progress>` : ''}
    ${u.available && u.notes ? `<details class="update-notes"><summary>新版说明</summary><pre>${esc(u.notes)}</pre></details>` : ''}
    <div class="actions"><button class="btn" id="checkUpdateBtn" ${busy || !native() ? 'disabled' : ''}>${icon('refresh-cw')}检查更新</button>
    ${u.available ? `<button class="btn primary" id="applyUpdateBtn" ${busy ? 'disabled' : ''}>${icon(u.downloaded ? 'power' : 'download')}${u.downloaded ? '安装并重启' : '下载更新'}</button>` : ''}</div>
    <p class="hint">更新从 GitHub Release 下载并验证签名。下载可在后台进行；安装前请结束研究任务并停止网关与 Science。</p>`;
  $('#checkUpdateBtn').onclick = () => checkForUpdates();
  if ($('#applyUpdateBtn')) $('#applyUpdateBtn').onclick = () => u.downloaded ? installAppUpdate() : downloadAppUpdate();
  icons();
}

async function checkForUpdates(silent = false) {
  if (!native() || ['checking', 'downloading', 'installing'].includes(updateState.phase)) return;
  updateState.phase = 'checking'; updateState.error = ''; renderUpdater();
  try {
    const result = await call('check_update');
    if (!result || typeof result.available !== 'boolean') throw new Error('更新服务返回了无效信息，请稍后重试。');
    Object.assign(updateState, result);
    updateState.phase = result.available ? result.downloaded ? 'downloaded' : 'available' : 'checked';
    if (result.available && !silent) notice(`发现新版本 ${result.version}，可在应用内下载更新。`);
  } catch (e) {
    updateState.error = String(e.message || e);
    updateState.phase = updateState.available ? updateState.downloaded ? 'downloaded' : 'available' : 'idle';
    if (!silent) notice(updateState.error, true);
  }
  renderUpdater();
}

async function downloadAppUpdate() {
  if (['checking', 'downloading', 'installing'].includes(updateState.phase)) return;
  updateState.phase = 'downloading'; updateState.error = ''; updateState.downloadedBytes = 0; updateState.total = null; renderUpdater();
  try {
    const progress = new window.__TAURI__.core.Channel();
    progress.onmessage = ({ downloaded, total }) => {
      updateState.downloadedBytes = downloaded; updateState.total = total; renderUpdater();
    };
    Object.assign(updateState, await call('download_update', { onProgress: progress }));
    updateState.phase = 'downloaded'; notice('更新包已下载并验证，可在任务结束后安装。');
  } catch (e) {
    updateState.error = String(e.message || e); updateState.phase = 'available'; notice(updateState.error, true);
  }
  renderUpdater();
}

async function installAppUpdate() {
  if (updateState.phase !== 'downloaded') return;
  try { await refreshRuntime(); }
  catch (e) { updateState.error = String(e.message || e); renderUpdater(); return; }
  if (state.runtime.proxy === 'green') {
    updateState.error = '请先在提供商页面停止网关与 Science，结束研究任务后再安装更新。'; renderUpdater(); return;
  }
  if (!await confirmAction('安装更新并重启？', `即将安装 ${updateState.version} 并重启 CSSwitch。提供商配置、密钥和对话记录会保留。`)) return;
  updateState.phase = 'installing'; updateState.error = ''; renderUpdater();
  try { await call('install_update'); }
  catch (e) { updateState.error = String(e.message || e); updateState.phase = 'downloaded'; notice(updateState.error, true); renderUpdater(); }
}

async function initUpdater() {
  if (!native()) return;
  try { updateState.current = await call('app_version'); } catch { /* Diagnostics also provides the version. */ }
  await checkForUpdates(true);
  setInterval(() => { if (!document.hidden) checkForUpdates(true); }, 6 * 60 * 60 * 1000);
}
