// CSSwitch Science UI translation. Local dictionary only; no network translation.
(() => {
  if (window.__csswitchZh) return;
  const dictionary = {
    'New':'新建', 'Customize':'自定义', 'Active':'进行中', 'Inactive':'未活动',
    'Dismiss':'关闭提示', 'Project actions':'项目操作', 'Session actions':'会话操作',
    'Skip to main content':'跳转到主要内容', 'Session tools':'会话工具',
    'Session tools moved up here':'会话工具已移至这里',
    'Plan, subagents, compute, notebook, and bookmarks now show up here. Switch sessions from the left sidebar.':'计划、子代理、计算资源、笔记本和书签现在位于这里。从左侧边栏切换会话。',
    'Setting up environments…':'正在配置环境…', 'Setting up environments...':'正在配置环境…',
    'This is an example session — it’s read-only. Start a new session to chat.':'这是只读示例会话。新建会话后即可开始对话。',
    "This is an example session — it's read-only. Start a new session to chat.":'这是只读示例会话。新建会话后即可开始对话。',
    'Plan first':'先制定计划', 'Side chat':'侧边对话', 'Continue as a new session':'作为新会话继续',
    'for more options':'查看更多选项', 'Subagents':'子代理', 'Notebook':'笔记本', 'Bookmarks':'书签',
    'Add bookmark':'添加书签', 'Remove bookmark':'移除书签', 'Show sidebar':'显示侧边栏',
    'Hide sidebar':'隐藏侧边栏', 'Toggle sidebar':'切换侧边栏',
    'sessions':'会话', 'artifacts':'产物', 'active now':'刚刚活跃', 'now':'刚刚',
    'New session':'新建会话', 'New chat':'新建对话', 'New conversation':'新建对话',
    'New project':'新建项目', 'Create project':'创建项目', 'Projects':'项目', 'Project':'项目',
    'Sessions':'会话', 'Session':'会话', 'Chats':'对话', 'Chat':'对话', 'History':'历史记录',
    'Recent':'最近', 'Recents':'最近', 'Recent sessions':'最近会话', 'All projects':'全部项目',
    'Search':'搜索', 'Search projects':'搜索项目', 'Search sessions':'搜索会话',
    'Settings':'设置', 'General':'通用', 'Appearance':'外观', 'Theme':'主题',
    'Light':'浅色', 'Dark':'深色', 'System':'跟随系统', 'Language':'语言',
    'Account':'账号', 'Profile':'个人资料', 'Organization':'组织', 'Sign in':'登录',
    'Sign out':'退出登录', 'Log in':'登录', 'Log out':'退出登录',
    'Connectors':'连接器', 'Connector':'连接器', 'Add connector':'添加连接器',
    'Custom connectors':'自定义连接器', 'Local connectors':'本地连接器',
    'Connected':'已连接', 'Disconnected':'未连接', 'Connecting…':'正在连接…',
    'Connecting...':'正在连接…', 'Not connected':'未连接', 'Connect':'连接',
    'Disconnect':'断开连接', 'Reconnect':'重新连接', 'Authorize':'授权',
    'Enabled':'已启用', 'Disabled':'已停用', 'Enable':'启用', 'Disable':'停用',
    'Skills':'技能', 'Skill':'技能', 'Add skill':'添加技能', 'Installed':'已安装',
    'Install':'安装', 'Uninstall':'卸载', 'Update':'更新', 'Updates':'更新',
    'Check for updates':'检查更新', 'Tools':'工具', 'Tool permissions':'工具权限',
    'Permissions':'权限', 'Allow':'允许', 'Deny':'拒绝', 'Ask':'询问',
    'Always allow':'始终允许', 'Ask every time':'每次询问', 'Approve':'批准',
    'Reject':'拒绝', 'Review':'审阅', 'Continue':'继续', 'Continue anyway':'仍然继续',
    'Cancel':'取消', 'Close':'关闭', 'Save':'保存', 'Save changes':'保存更改',
    'Delete':'删除', 'Remove':'移除', 'Rename':'重命名', 'Edit':'编辑',
    'Copy':'复制', 'Copied':'已复制', 'Copied!':'已复制', 'Copy link':'复制链接',
    'Copy message':'复制消息', 'Copy code':'复制代码', 'Download':'下载',
    'Export':'导出', 'Import':'导入', 'Upload':'上传', 'Upload files':'上传文件',
    'Files':'文件', 'File':'文件', 'Artifacts':'产物', 'Open':'打开',
    'Open in new tab':'在新标签页打开', 'Open folder':'打开文件夹',
    'Share':'分享', 'Archive':'归档', 'Unarchive':'取消归档', 'Archived':'已归档',
    'Star':'收藏', 'Unstar':'取消收藏', 'Starred':'已收藏', 'Favorite':'收藏',
    'Name':'名称', 'Description':'描述', 'Model':'模型', 'Models':'模型',
    'Default':'默认', 'Advanced':'高级', 'Advanced settings':'高级设置',
    'Network':'网络', 'Storage':'存储', 'Usage':'用量', 'Usage limits':'用量限制',
    'Privacy':'隐私', 'Security':'安全', 'About':'关于', 'Help':'帮助',
    'Documentation':'文档', 'Feedback':'反馈', 'Send feedback':'发送反馈',
    'Report a bug':'报告问题', 'Keyboard shortcuts':'键盘快捷键',
    'Notifications':'通知', 'Preferences':'偏好设置', 'Memory':'记忆',
    'Compute':'计算资源', 'Environment':'环境', 'Terminal':'终端',
    'Stop':'停止', 'Stop generating':'停止生成', 'Send':'发送', 'Send message':'发送消息',
    'Retry':'重试', 'Try again':'重试', 'Refresh':'刷新', 'Back':'返回',
    'Go back':'返回', 'Next':'下一步', 'Previous':'上一步', 'Done':'完成',
    'Confirm':'确认', 'Add':'添加', 'Create':'创建', 'Select':'选择',
    'Select all':'全选', 'Deselect all':'取消全选', 'Clear':'清除',
    'Loading':'正在加载', 'Loading…':'正在加载…', 'Loading...':'正在加载…',
    'No results':'没有结果', 'No results found':'未找到结果', 'Show more':'显示更多',
    'Show less':'收起', 'Expand':'展开', 'Collapse':'折叠', 'Details':'详情',
    'View details':'查看详情', 'Read more':'查看全文', 'Learn more':'了解更多',
    'Thinking':'思考中', 'Thinking…':'正在思考…', 'Running':'运行中',
    'Completed':'已完成', 'Failed':'失败', 'Paused':'已暂停', 'Pending':'等待中',
    'Resume':'继续', 'Pause':'暂停', 'Status':'状态', 'Activity':'活动',
    'Research':'研究', 'Research mode':'研究模式', 'Plan':'计划',
    'Message Claude':'向 Claude 发送消息', 'Ask Claude':'询问 Claude',
    'What can I help you with?':'有什么可以帮你？', 'How can I help?':'有什么可以帮你？',
    'What would you like to explore?':'你想探索什么？',
    'Search conversations':'搜索对话', 'Search chats':'搜索对话',
    'Message Claude Science':'向 Claude Science 发送消息',
    'Type a message...':'输入消息…', 'Type a message…':'输入消息…',
    'Attach files':'添加附件', 'Add files':'添加文件', 'New task':'新建任务',
  };
  // Unknown UI copy stays English. Never translate arbitrary prose or substrings.
  const protectedSelector = 'script,style,pre,code,textarea,input,[contenteditable="true"],[contenteditable=""],[data-csswitch-no-translate],.prose,.markdown,.markdown-body,.markdown-content,[data-message-content],[data-user-content],[data-testid*="session-title"],[data-testid*="project-name"]';
  const controls = 'button,label,nav,aside,header,footer,legend,option,a[href^="#"],[class*="sidebar"],.cds-tooltip,[role="button"],[role="menu"],[role="menuitem"],[role="tab"],[role="tooltip"],[role="dialog"],[role="status"],[role="alert"],h1,h2,h3,h4';
  const attributes = ['title','aria-label','placeholder','data-placeholder'];
  const texts = new Map(), attrs = new Map();
  let enabled = false, queued = false;
  const translate = value => {
    const stripped = value.trim();
    if (dictionary[stripped]) return value.replace(stripped, dictionary[stripped]);
    const patterns = [
      [/^Account menu — (.+)$/, m => `账号菜单 — ${m[1]}`],
      [/^(\d+) environments? failed$/, m => `${m[1]} 个环境配置失败`],
      [/^(\d+) steps?$/, m => `${m[1]} 个步骤`],
      [/^(\d+) results?$/, m => `${m[1]} 条结果`],
      [/^GENERATED · (\d+)$/, m => `生成产物 · ${m[1]}`],
      [/^\+(\d+) more$/, m => `还有 ${m[1]} 个`],
    ];
    for (const [pattern, render] of patterns) { const match = stripped.match(pattern); if (match) return value.replace(stripped, render(match)); }
    return value;
  };
  const safe = element => element && !element.closest(protectedSelector);
  function scan() {
    queued = false;
    if (!enabled || !document.body) return;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node;
    while ((node = walker.nextNode())) {
      const parent = node.parentElement;
      if (!safe(parent) || !parent.closest(controls)) continue;
      const previous = texts.get(node);
      if (previous && node.nodeValue === previous.translated) continue;
      const original = node.nodeValue, translated = translate(original);
      if (translated !== original) { texts.set(node, {original, translated}); node.nodeValue = translated; }
      else if (previous) texts.delete(node);
    }
    document.querySelectorAll('[title],[aria-label],[placeholder],[data-placeholder]').forEach(element => {
      if (!safe(element) && !element.matches('input,textarea')) return;
      let saved = attrs.get(element);
      for (const key of attributes) {
        const value = element.getAttribute(key);
        if (value === null || saved?.[key]?.translated === value) continue;
        const translated = translate(value);
        if (translated !== value) {
          if (!saved) { saved = {}; attrs.set(element, saved); }
          saved[key] = {original:value, translated}; element.setAttribute(key, translated);
        } else if (saved) delete saved[key];
      }
    });
    // Release detached React nodes after each pass.
    for (const node of texts.keys()) if (!node.isConnected) texts.delete(node);
    for (const element of attrs.keys()) if (!element.isConnected) attrs.delete(element);
  }
  function setEnabled(value) {
    enabled = value === true;
    if (enabled) scan();
    else {
      for (const [node, saved] of texts) if (node.nodeValue === saved.translated) node.nodeValue = saved.original;
      for (const [element, saved] of attrs) for (const [key, value] of Object.entries(saved))
        if (element.getAttribute(key) === value.translated) element.setAttribute(key, value.original);
      texts.clear(); attrs.clear();
    }
  }
  const observer = new MutationObserver(() => {
    if (enabled && !queued) { queued = true; requestAnimationFrame(scan); }
  });
  const poll = async () => {
    try {
      const response = await fetch('/__csswitch/config', {cache:'no-store', credentials:'omit'});
      if (response.ok) { const config = await response.json(); if (config.enabled !== enabled) setEnabled(config.enabled); }
    } catch { /* Keep current UI language through a temporary service outage. */ }
  };
  window.__csswitchZh = {setEnabled, dictionary};
  function start() {
    observer.observe(document.body, {subtree:true, childList:true, characterData:true, attributes:true, attributeFilter:attributes});
    poll(); setInterval(poll, 3000);
  }
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, {once:true});
})();
