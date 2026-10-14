// Local Markdown rendering; remote images never load while reading history.
function markdown(text) {
  if (!window.marked || !window.DOMPurify) return `<p>${esc(text).replace(/\n/g, '<br>')}</p>`;
  const renderer = new window.marked.Renderer();
  renderer.html = token => esc(token.text);
  renderer.image = token => `<span class="attachment-note">${esc(token.text || '图片附件')}</span>`;
  const html = window.marked.parse(String(text || ''), { renderer, breaks: true, gfm: true });
  return window.DOMPurify.sanitize(html, {
    ALLOWED_TAGS: ['p', 'br', 'strong', 'em', 'del', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol', 'li', 'blockquote', 'pre', 'code', 'hr', 'a', 'table', 'thead', 'tbody', 'tr', 'th', 'td', 'span'],
    ALLOWED_ATTR: ['href', 'title', 'class', 'start'], ALLOW_DATA_ATTR: false,
  });
}
function bindMarkdown(box) {
  box.querySelectorAll('.markdown a').forEach(link => {
    if (!/^https?:\/\//i.test(link.getAttribute('href') || '')) link.removeAttribute('href');
    link.target = '_blank'; link.rel = 'noopener noreferrer';
  });
  box.querySelectorAll('.markdown pre').forEach(pre => {
    if (pre.querySelector('button')) return;
    const bar = document.createElement('div'); bar.className = 'code-toolbar';
    const lang = document.createElement('span'); lang.textContent = pre.querySelector('code')?.className.replace('language-', '') || '代码';
    const copy = document.createElement('button'); copy.className = 'code-copy'; copy.textContent = '复制'; copy.setAttribute('aria-label', '复制代码');
    copy.onclick = async () => {
      try { await navigator.clipboard.writeText(pre.querySelector('code')?.textContent || ''); copy.textContent = '已复制'; }
      catch { notice('无法复制，请选择代码后复制。', true); }
    };
    bar.append(lang, copy); pre.prepend(bar);
  });
}
