/* Align saved picker choices with the active catalog before native initialization. */
(() => {
  const native = __CSSWITCH_NATIVE_IDS__;
  if (!Object.keys(native).length) return;
  try {
    for (const key of ['operon-default-model', 'operon-subagent-model']) {
      const value = localStorage.getItem(key);
      if (native._four_slots && Object.values(native).includes(value)) continue;
      const match = native._four_slots
        ? /^claude-(?:(fable|opus|sonnet|haiku)-\d{1,2}(?:-\d{1,2})*|\d{1,2}(?:-\d{1,2})*-(fable|opus|sonnet|haiku))(?:-20\d{6}|-latest)?$/i.exec(value || '')
        : /^claude-(opus|sonnet|haiku)-99-\d{2}-20\d{6}$/.exec(value || '');
      let kind = (match?.[1] || match?.[2])?.toLowerCase();
      if (!native._four_slots && kind === 'haiku') kind = 'sonnet';
      if (kind && native[kind]) localStorage.setItem(key, native[kind]);
    }
    localStorage.removeItem('csswitch-science-default-model');
  } catch { /* Restricted storage leaves the native application usable. */ }
})();
