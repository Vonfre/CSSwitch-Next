/* Local artifact actions for Science's CSSwitch page entry. No downloads or path guessing. */
(() => {
  if (window.__csswitchFilesLoaded) return;
  window.__csswitchFilesLoaded = true;
  const menus = '[role="menu"][aria-label="Artifact actions"],[role="menu"][aria-label="File actions"],[role="menu"][aria-label="文件操作"],[data-testid="file-context-menu"]';
  const installed = new Map();
  let enabled = false, token = '', busy = false, scheduled = false, toast, toastTimer;
  const invalid = () => { throw new Error('无法确认此菜单的文件或版本，请重新打开文件预览后重试。'); };
  function identity(id, version) {
    if (typeof id !== 'string' || !/^[\w-]{1,128}$/.test(id) ||
        (version != null && (typeof version !== 'string' || !/^[\w-]{1,128}$/.test(version)))) return invalid();
    return { artifactId: id, versionId: version ?? null };
  }
  function previewVersion(viewer, id) {
    const found = new Set(), pending = viewer.child ? [viewer.child] : [];
    let visited = 0;
    while (pending.length && visited++ < 600) {
      const node = pending.pop(), props = node.memoizedProps;
      const match = props?.artifact?.id === id && typeof props.versionId === 'string';
      if (match) found.add(props.versionId);
      if (!match && node.child) pending.push(node.child);
      if (node.sibling) pending.push(node.sibling);
    }
    if (pending.length || found.size !== 1) return invalid();
    return [...found][0];
  }
  function menuIdentity(menu) {
    // These are native React view models, scoped to the clicked menu. A filename
    // is not an identity: different conversations and versions can share one.
    let node = menu[Object.keys(menu).find(key => key.startsWith('__reactFiber$'))];
    if (!node) return invalid();
    let root = node;
    for (let n = 0; root.return && n < 200; n++) root = root.return;
    if (root.stateNode?.current && root.stateNode.current !== root) node = node.alternate;
    let position;
    for (let depth = 0; node && depth < 16; depth++, node = node.return) {
      const props = node.memoizedProps || {};
      if (props.selectedCount > 1) throw new Error('请一次选择一个文件。');
      if (typeof props.onClose === 'function' && Number.isFinite(props.x) && Number.isFinite(props.y)) {
        position = { x: props.x, y: props.y };
      }
      if (position) {
        const choices = new Map();
        let hook = node.memoizedState, count = 0;
        // Current native viewers already exceed 160 hooks. Keep a generous
        // bound while still refusing a truncated/ambiguous menu state.
        while (hook && count++ < 1024) {
          const state = hook.memoizedState;
          for (const item of [state, state?.current]) {
            if (item?.position?.x !== position.x || item?.position?.y !== position.y || !item.data) continue;
            const data = item.data;
            let matched = false;
            for (const [idKey, versionKey] of [['id', 'version_id'], ['artifact_id', 'version_id'], ['artifactId', 'versionId']]) {
              if (typeof data[idKey] !== 'string') continue;
              const value = identity(data[idKey], data[versionKey]);
              choices.set(JSON.stringify(value), value); matched = true;
            }
            if (!matched) return invalid();
          }
          hook = hook.next;
        }
        if (hook || choices.size > 1) return invalid();
        if (choices.size === 1) return [...choices.values()][0];
      }
      if (props.file?.ref) {
        const ref = props.file.ref;
        if (ref.kind === 'artifact') return identity(ref.artifactId, ref.versionId);
        throw new Error('此文件没有可验证的本地存储记录，请使用 Science 原有的文件操作。');
      }
      if (props.resolvedArtifactId != null) return identity(props.resolvedArtifactId, props.resolvedVersionId);
      if (props.tile?.artifactId) return identity(props.tile.artifactId, previewVersion(node, props.tile.artifactId));
      if (props.tab?.artifactId) return identity(props.tab.artifactId, props.tab.versionId);
      if (props.artifact?.id) return identity(props.artifact.id, props.versionId ?? props.artifact.version_id);
    }
    return invalid();
  }
  function announce(message, error = false) {
    clearTimeout(toastTimer); toast?.remove();
    toast = document.createElement('div');
    toast.dataset.csswitchFilesNotice = '';
    toast.setAttribute('role', error ? 'alert' : 'status');
    toast.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;max-width:min(440px,calc(100vw - 32px));box-sizing:border-box;padding:12px 38px 12px 16px;border-radius:12px;background:#292826;color:#faf9f6;box-shadow:0 4px 24px #0003;font:14px/1.5 system-ui,sans-serif;overflow-wrap:anywhere;';
    const text = document.createElement('span'); text.textContent = message;
    const close = document.createElement('button'); close.type = 'button'; close.textContent = '×'; close.setAttribute('aria-label', '关闭文件操作提示');
    close.style.cssText = 'position:absolute;right:10px;top:8px;border:0;background:transparent;color:inherit;font-size:20px;cursor:pointer;';
    close.onclick = () => toast?.remove(); toast.append(text, close); document.body.append(toast);
    toastTimer = setTimeout(() => toast?.remove(), error ? 12000 : 4500);
  }
  async function perform(event, menu, action) {
    event.preventDefault(); event.stopPropagation();
    if (!event.isTrusted || !enabled || !menu.isConnected || busy) return;
    busy = true;
    installed.forEach(({group}) => group.querySelectorAll('button').forEach(button => { button.disabled = true; }));
    try {
      const target = menuIdentity(menu);
      const headers = { 'Content-Type': 'application/json', 'X-CSSwitch-Files': token };
      const bootstrap = window.__OPERON__;
      if (bootstrap?.token) {
        const base = new URL(bootstrap.baseUrl || '/', location.origin);
        if (base.origin !== location.origin || base.username || base.password) return invalid();
        headers.Authorization = 'Bearer ' + bootstrap.token;
      }
      const response = await fetch('/__csswitch/files/action', {
        method: 'POST', credentials: 'same-origin', redirect: 'error', headers,
        body: JSON.stringify({ ...target, action }), signal: AbortSignal.timeout(30000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || '本地文件打开失败。');
      announce(result.message || '已打开本地文件。');
    } catch (error) {
      announce(error.name === 'TimeoutError' ? '打开请求超时，请先检查本机应用是否已经打开文件。' : error.message || '本地文件打开失败。', true);
    } finally {
      busy = false;
      installed.forEach(({group}) => group.querySelectorAll('button').forEach(button => { button.disabled = false; }));
    }
  }
  function fit(menu) {
    menu.style.maxHeight = 'calc(100vh - 16px)'; menu.style.overflowY = 'auto';
    menu.style.maxWidth = 'calc(100vw - 16px)';
    const rect = menu.getBoundingClientRect();
    if (getComputedStyle(menu).position === 'fixed') {
      if (rect.bottom > innerHeight - 8) menu.style.top = Math.max(8, innerHeight - rect.height - 8) + 'px';
      if (rect.right > innerWidth - 8) menu.style.left = Math.max(8, innerWidth - rect.width - 8) + 'px';
    }
  }
  function mount(menu) {
    const group = document.createElement('div'); group.dataset.csswitchFiles = ''; group.setAttribute('role', 'group'); group.setAttribute('aria-label', '本地文件');
    group.style.cssText = 'border-top:1px solid color-mix(in srgb,currentColor 12%,transparent);margin-top:4px;padding-top:4px;';
    const sample = menu.querySelector('button:not([disabled]),[role="menuitem"]');
    for (const [action, label, drawing] of [
      ['reveal', '在文件夹中显示', 'M3 7V5h6l2 2h10v12H3Z'],
      ['open', '用默认应用打开', 'M14 3h7v7M21 3l-11 11M10 3H3v18h18v-7'],
      ['edit', '用文本编辑器打开', 'm16 3 5 5-12 12H4v-5Z M13 6l5 5'],
    ]) {
      const button = document.createElement('button'); button.type = 'button'; button.className = sample?.className || '';
      button.dataset.csswitchFileAction = action; button.setAttribute('role', 'menuitem'); button.disabled = busy;
      button.title = action === 'reveal' ? '定位当前版本的实际本地文件，不下载副本' : '打开实际文件；保存编辑会更改该文件';
      button.style.cssText = 'display:flex;align-items:center;gap:8px;width:100%;padding:8px 12px;border:0;border-radius:5px;text-align:left;background:transparent;color:inherit;font:inherit;white-space:nowrap;cursor:pointer;';
      if (sample) button.style.font = getComputedStyle(sample).font;
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('width', '16'); svg.setAttribute('height', '16'); svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '1.5'); svg.setAttribute('aria-hidden', 'true');
      const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', drawing); svg.append(path);
      button.append(svg, document.createTextNode(label));
      const highlight = () => { button.style.background = 'color-mix(in srgb,currentColor 9%,transparent)'; };
      const reset = () => { button.style.background = 'transparent'; };
      button.onmouseenter = button.onfocus = highlight; button.onmouseleave = button.onblur = reset;
      button.addEventListener('click', event => perform(event, menu, action));
      button.addEventListener('keydown', event => {
        if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault(); event.stopPropagation();
        const items = [...menu.querySelectorAll('button:not([disabled]),[role="menuitem"]:not([disabled])')];
        const index = items.indexOf(button);
        items[event.key === 'Home' ? 0 : event.key === 'End' ? items.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
      });
      group.append(button);
    }
    const saved = Object.fromEntries(['maxHeight', 'maxWidth', 'overflowY', 'top', 'left'].map(key => [key, menu.style[key]]));
    installed.set(menu, { group, saved }); menu.append(group); fit(menu);
  }
  function unmount(menu, record) {
    record.group.remove(); Object.assign(menu.style, record.saved); installed.delete(menu);
  }
  function scan() {
    scheduled = false;
    for (const [menu, record] of installed) {
      if (!enabled || !menu.isConnected || !menu.matches(menus) || !menu.contains(record.group)) unmount(menu, record);
    }
    if (enabled) document.querySelectorAll(menus).forEach(menu => { if (!installed.has(menu)) mount(menu); });
    observer.takeRecords();
  }
  function schedule() { if (!scheduled) { scheduled = true; setTimeout(scan, 0); } }
  const observer = new MutationObserver(schedule);
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['aria-label', 'data-testid'] });
  window.addEventListener('resize', () => installed.forEach((_, menu) => fit(menu)));
  async function refresh() {
    try {
      const response = await fetch('/__csswitch/files/config', { credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(2500) });
      if (!response.ok) throw new Error();
      const config = await response.json(); enabled = config.enabled === true && typeof config.token === 'string'; token = enabled ? config.token : '';
    } catch { enabled = false; token = ''; }
    schedule();
  }
  refresh(); setInterval(refresh, 3000);
})();
