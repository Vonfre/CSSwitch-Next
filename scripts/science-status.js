/* Attribute native retry status to the configured third-party API provider. */
(() => {
  if (window.__csswitchStatusLoaded) return;
  window.__csswitchStatusLoaded = true;
  fetch('/__csswitch/status/config', { credentials: 'same-origin', cache: 'no-store' })
    .then(response => response.ok ? response.json() : null)
    .then(config => {
      if (!config?.enabled) return;
      const scan = () => {
        document.querySelectorAll('[role="status"] a[href^="https://status.claude.com"]').forEach(link => {
          const status = link.closest('[role="status"]');
          const walker = document.createTreeWalker(status, NodeFilter.SHOW_TEXT);
          let text;
          while ((text = walker.nextNode())) {
            const value = text.nodeValue
              .replace('Claude is temporarily unavailable — retrying', '当前 API 服务商暂时不可用，正在重试')
              .replace('Claude is at capacity — retrying', '当前 API 服务商繁忙，正在重试');
            if (text.nodeValue !== value) text.nodeValue = value;
          }
          link.hidden = true;
          if (link.previousElementSibling?.textContent.trim() === '·') link.previousElementSibling.hidden = true;
          status.title = '当前使用第三方 API，Claude 官方状态页不反映此服务商的状态。';
        });
      };
      let scheduled = false;
      new MutationObserver(() => {
        if (scheduled) return;
        scheduled = true;
        requestAnimationFrame(() => { scheduled = false; scan(); });
      }).observe(document.documentElement, { childList: true, characterData: true, subtree: true });
      scan();
    }).catch(() => {});
})();
