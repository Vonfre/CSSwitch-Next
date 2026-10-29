/* Retire legacy picker preferences before the native application initializes. */
(() => {
  const native = __CSSWITCH_NATIVE_IDS__;
  if (!Object.keys(native).length) return;
  try {
    for (const key of ['operon-default-model', 'operon-subagent-model']) {
      const value = localStorage.getItem(key);
      const match = /^claude-(opus|sonnet|haiku)-99-\d{2}-20\d{6}$/.exec(value || '');
      const kind = match?.[1] === 'haiku' ? 'sonnet' : match?.[1];
      if (kind && native[kind]) localStorage.setItem(key, native[kind]);
    }
    localStorage.removeItem('csswitch-science-default-model');
  } catch { /* Restricted storage leaves the native application usable. */ }
})();
