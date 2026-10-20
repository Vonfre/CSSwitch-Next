// Shared contextual help; plain text, keyboard and touch accessible.
function help(label, text) {
  return `<button type="button" class="help-btn" aria-label="${esc(label)}说明" data-help="${esc(text)}">${icon('circle-help')}</button>`;
}

let helpOwner = null;
function closeHelp() {
  const popover = document.getElementById('helpPopover');
  if (!popover) return;
  if (popover.hidePopover && popover.matches(':popover-open')) popover.hidePopover();
  popover.hidden = true;
  helpOwner?.removeAttribute('aria-describedby');
  helpOwner = null;
}
function showHelp(button) {
  closeHelp();
  const popover = document.getElementById('helpPopover');
  helpOwner = button; popover.textContent = button.dataset.help; popover.hidden = false;
  button.setAttribute('aria-describedby', 'helpPopover');
  if (popover.showPopover) popover.showPopover();
  const rect = button.getBoundingClientRect(), box = popover.getBoundingClientRect();
  popover.style.left = Math.max(12, Math.min(rect.left, innerWidth - box.width - 12)) + 'px';
  popover.style.top = Math.max(12, rect.bottom + box.height + 12 < innerHeight ? rect.bottom + 8 : rect.top - box.height - 8) + 'px';
}
document.addEventListener('click', e => {
  const button = e.target.closest('[data-help]');
  if (button) { helpOwner === button ? closeHelp() : showHelp(button); return; }
  if (!e.target.closest('#helpPopover')) closeHelp();
});
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeHelp(); });
document.addEventListener('focusout', e => { if (e.target === helpOwner) closeHelp(); });
window.addEventListener('resize', closeHelp);
document.addEventListener('scroll', e => { if (!e.target.closest?.('#helpPopover')) closeHelp(); }, true);

const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
function applyTheme(preference = localStorage.getItem('csswitch-theme') || 'system') {
  document.documentElement.dataset.theme = preference === 'system' ? systemTheme.matches ? 'dark' : 'light' : preference;
  if (document.getElementById('themePreference')) document.getElementById('themePreference').value = preference;
}
systemTheme.addEventListener('change', () => {
  if (!localStorage.getItem('csswitch-theme') || localStorage.getItem('csswitch-theme') === 'system') applyTheme('system');
});

// Fixed local font stacks: no downloads, and safe fallbacks on other platforms.
const uiFonts = [
  { id: 'system', label: '系统默认', family: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' },
  { id: 'pingfang', label: '苹方', family: '"PingFang SC", "Microsoft YaHei", sans-serif' },
  { id: 'helvetica', label: 'Helvetica', family: '"Helvetica Neue", Helvetica, Arial, "PingFang SC", "Microsoft YaHei", sans-serif' },
  { id: 'songti', label: '宋体', family: '"Songti SC", SimSun, "Noto Serif CJK SC", serif' },
];
function applyFont(preference = localStorage.getItem('csswitch-font')) {
  const font = uiFonts.find(item => item.id === preference) || uiFonts[0];
  document.documentElement.style.setProperty('--ui-font', font.family);
  document.documentElement.dataset.font = font.id;
  const select = document.getElementById('fontPreference');
  if (select) select.value = font.id;
}
