import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function setup(page, accounts = true) {
  await page.addInitScript(({ accounts }) => {
    window.subscriptionCalls = [];
    window.subscriptionUsageCalls = 0;
    window.subscriptionUsageResult = { accounts: Object.fromEntries(['a@example.test', 'b@example.test'].map((id, i) => [id, {
      status: 'ok', fetched_at: Date.now() / 1000, stale: false,
      windows: [{ key: 'five_hour', label: '5 小时', remaining_percent: i ? 8 : 67.5, resets_at: Date.now() / 1000 + 3600 },
        { key: 'seven_day', label: '一周', remaining_percent: i ? 30 : 82, resets_at: Date.now() / 1000 + 86400 }]
    }])) };
    window.subscriptionStatus = { installed: true, gateway: true, science: { running: true, pid: 123 },
      connected: true, ready_accounts: accounts ? 2 : 0, mode: 'manual', active_requests: 0,
      native: { enabled: false, account_id: accounts ? 'a@example.test' : null, last_switch: {} },
      accounts: accounts ? ['a@example.test', 'b@example.test'].map((user, index) => ({ id: user, user, plan: 'pro', on: true, current: index === 1, cooldown_until: 0 })) : [] };
    window.__TAURI__ = { core: { invoke: async (cmd, args) => {
      if (cmd === 'get_config') return { mode: 'official', connections: [], provider: '' };
      if (cmd === 'status') return { proxy: 'amber', sandbox: 'amber' };
      if (cmd === 'check_update') return { available: false };
      if (cmd === 'app_version') return '0.8.17';
      if (cmd === 'subscription_manage') {
        if (args.operation === 'usage') {
          window.subscriptionUsageCalls++;
          window.lastUsageParams = args.params;
          if (window.subscriptionUsageWait) await new Promise(resolve => { window.resolveSubscriptionUsage = resolve; });
          if (window.subscriptionUsageFail) throw new Error('network');
          return window.subscriptionUsageResult;
        }
        window.subscriptionCalls.push(args);
        if (args.operation === 'status') return window.subscriptionStatus;
        if (args.operation === 'sync_login') window.subscriptionStatus.native.account_id = args.params.id;
        if (args.operation === 'hot_enable') { window.subscriptionStatus.hot_mode = true; window.subscriptionStatus.mode = 'auto'; }
        if (args.operation === 'select') {
          window.subscriptionStatus.accounts.forEach(a => { a.current = a.id === args.params.id; });
          window.subscriptionStatus.mode = 'manual';
        }
        if (args.operation === 'auto') window.subscriptionStatus.mode = 'auto';
        if (args.operation === 'priority') { window.subscriptionStatus.preferred_id = args.params.enabled === false ? null : args.params.id; if (args.params.enabled !== false) window.subscriptionStatus.next_account_id = args.params.id; window.subscriptionStatus.mode = 'auto'; }
        if (window.subscriptionFail) throw new Error('目标账号有独立或更新过的历史，未覆盖。');
        if (args.operation === 'switch') {
          window.subscriptionStatus.native = { enabled: true, account_id: args.params.id, last_switch: { phase: 'complete', at: 1794140000 } };
          window.subscriptionStatus.gateway = false;
          window.subscriptionStatus.connected = false;
        }
        if (args.operation === 'delete') {
          window.subscriptionStatus.accounts = window.subscriptionStatus.accounts.filter(a => a.id !== args.params.id);
          window.subscriptionStatus.ready_accounts = window.subscriptionStatus.accounts.length;
          return { message: '账号已删除。本地项目、会话和历史备份已保留。' };
        }
        return { message: '已切换 Science 真实登录账号并验证成功。原会话请点击 Resume。' };
      }
      throw new Error(`Unexpected command: ${cmd}`);
    } } };
  }, { accounts });
  await page.goto('/');
  await page.locator('button[data-view="subscriptions"]').click();
}

test('shows native identity instead of the different inference selection', async ({ page }) => {
  await setup(page);
  await expect(page.locator('[data-subscription-id="a@example.test"]')).toBeDisabled();
  await expect(page.locator('[data-subscription-id="b@example.test"]')).toBeEnabled();
  await expect(page.locator('.subscription-routing')).toContainText('a@example.test');
  await expect(page.locator('.subscription-policy').first()).toContainText('重启');
});

test('priority switches change the account without changing native login', async ({ page }) => {
  await setup(page);
  await page.locator('#subscriptionHotEnable').click();
  const a = page.getByRole('switch', {name:'优先消耗 a@example.test'});
  const b = page.getByRole('switch', {name:'优先消耗 b@example.test'});
  await a.click();
  await expect(a).toBeChecked(); await expect(b).not.toBeChecked();
  await b.click();
  await expect(b).toBeChecked(); await expect(a).not.toBeChecked();
  await expect(page.locator('.subscription-login-identity')).toContainText('a@example.test');
  await expect(page.locator('#subscriptionPriority')).toHaveCount(0);
  await expect(page.getByText('用于后续推理', {exact:true})).toHaveCount(0);
  const calls = await page.evaluate(() => window.subscriptionCalls.map(c => c.operation));
  expect(calls).toContain('priority'); expect(calls).not.toContain('select'); expect(calls).not.toContain('switch');
});

test('disconnected hot mode does not claim routing is live', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.subscriptionStatus.hot_mode = true; window.subscriptionStatus.connected = false; });
  await page.locator('#refreshBtn').click();
  await expect(page.getByRole('button', {name:'连接免重启模式'})).toBeVisible();
  await expect(page.locator('[data-hot-priority="b@example.test"]')).toBeDisabled();
  await expect(page.locator('.subscription-routing')).toContainText('暂无回复记录');
});

test('switches real login, shows resume handoff and permits switching back', async ({ page }) => {
  await setup(page);
  await page.locator('[data-subscription-id="b@example.test"]').click();
  await expect(page.locator('.subscription-routing')).toContainText('b@example.test');
  await expect(page.locator('.subscription-routing')).toContainText('本地会话已保留');
  await expect(page.locator('.subscription-service-bar')).toContainText('订阅网关不参与推理');
  await expect(page.locator('#notice')).toContainText('Resume');
  await page.locator('[data-subscription-id="a@example.test"]').click();
  expect(await page.evaluate(() => window.subscriptionCalls.filter(c => c.operation !== 'status')))
    .toEqual([{ operation: 'switch', params: { id: 'b@example.test' } }, { operation: 'switch', params: { id: 'a@example.test' } }]);
});

test('empty accounts invite official authorization', async ({ page }) => {
  await setup(page, false);
  await expect(page.locator('#content')).toContainText('尚未登录');
  await page.locator('#subscriptionLogin').click();
  expect(await page.evaluate(() => window.subscriptionCalls.filter(c => c.operation !== 'status').map(c => c.operation)))
    .toEqual(['login']);
});

test('deletion requires confirmation, removes the row and protects the configured native account', async ({ page }) => {
  await setup(page);
  await expect(page.getByRole('button', { name: '删除账号 a@example.test', exact: true })).toBeDisabled();
  const remove = page.getByRole('button', { name: '删除账号 b@example.test', exact: true });
  await remove.click();
  await expect(page.locator('#confirmBody')).toContainText('b@example.test');
  await expect(page.locator('#confirmBody')).toContainText('不会取消官方订阅');
  await page.locator('#confirmDialog').getByRole('button', { name: '取消', exact: true }).click();
  expect(await page.evaluate(() => window.subscriptionCalls.some(c => c.operation === 'delete'))).toBe(false);
  await expect(remove).toBeVisible();
  await remove.click();
  await page.locator('#confirmDialog').getByRole('button', { name: '确认', exact: true }).click();
  await expect(remove).toHaveCount(0);
  await expect(page.locator('.subscription-count')).toHaveText('1');
  await expect(page.locator('#notice')).toContainText('账号已删除');
  expect(await page.evaluate(() => window.subscriptionCalls.filter(c => c.operation === 'delete')))
    .toEqual([{ operation: 'delete', params: { id: 'b@example.test' } }]);
  await page.evaluate(() => { window.subscriptionStatus.science.running = false; });
  await page.locator('#refreshBtn').click();
  await expect(page.getByRole('button', { name: '删除账号 a@example.test', exact: true })).toBeDisabled();
});

test('deletion errors preserve accounts and unfinished switches disable deletion', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.subscriptionFail = true; });
  const remove = page.getByRole('button', { name: '删除账号 b@example.test', exact: true });
  await remove.click();
  await page.locator('#confirmDialog').getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('未覆盖');
  await expect(remove).toBeVisible();
  await expect(page.locator('.subscription-count')).toHaveText('2');
  await page.evaluate(() => { window.subscriptionStatus.native.last_switch = { phase: 'recovery_required' }; });
  await page.locator('#refreshBtn').click();
  await expect(remove).toBeDisabled();
});

test('deleting the last unconfigured account returns to the empty state', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => {
    window.subscriptionStatus.native.account_id = null;
    window.subscriptionStatus.accounts = window.subscriptionStatus.accounts.slice(1);
    window.subscriptionStatus.ready_accounts = 1;
  });
  await page.locator('#refreshBtn').click();
  await page.getByRole('button', { name: '删除账号 b@example.test', exact: true }).click();
  await page.locator('#confirmDialog').getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('.subscription-count')).toHaveText('0');
  await expect(page.locator('.subscription-empty')).toBeVisible();
});

test('Windows shows detected Unicode data path and first login guidance without overflow', async ({ page }) => {
  await setup(page, false);
  await page.evaluate(() => {
    window.subscriptionStatus.data_dir = 'D:\\科研 数据\\很长的本机对话目录\\Claude Science';
    window.subscriptionStatus.history_found = true;
    window.subscriptionStatus.native.initial_login_required = true;
    window.subscriptionStatus.native.supported_versions = '0.1.71 / 0.1.72（Windows）';
  });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('.subscription-routing')).toContainText('已发现本地会话库');
  await expect(page.locator('.subscription-routing')).toContainText('请先在官方 Science 完成一次登录');
  await expect(page.locator('.subscription-routing')).toContainText('D:\\科研 数据');
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
});

test('failed switch keeps native account and offers recovery for interrupted handoff', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.subscriptionFail = true; });
  await page.locator('[data-subscription-id="b@example.test"]').click();
  await expect(page.locator('#notice')).toContainText('未覆盖');
  await expect(page.locator('.subscription-routing')).toContainText('a@example.test');
  await page.evaluate(() => {
    window.subscriptionFail = false;
    window.subscriptionStatus.native.last_switch = { phase: 'recovery_required' };
  });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('[data-subscription-id="b@example.test"]')).toBeDisabled();
  await page.locator('#subscriptionRecover').click();
  expect(await page.evaluate(() => window.subscriptionCalls.at(-2).operation)).toEqual('recover');
});

test('shows callback failure and fits desktop, dark and mobile layouts', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.subscriptionStatus.login = { status: 'error', message: '兑换授权失败（HTTP 403）。' }; });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('.subscription-login-status')).toContainText('兑换授权失败');
  mkdirSync('../artifacts', { recursive: true });
  await page.screenshot({ path: '../artifacts/subscriptions-native-desktop.png', fullPage: true, animations: 'disabled' });
  await page.locator('#themeBtn').click();
  await page.screenshot({ path: '../artifacts/subscriptions-native-dark.png', fullPage: true, animations: 'disabled' });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({ path: '../artifacts/subscriptions-native-mobile.png', fullPage: true, animations: 'disabled' });
  await page.locator('.subscription-help summary').click();
  await expect(page.locator('.subscription-help')).toHaveAttribute('open');
});

test('shows remaining official limits, reset times and stale or missing values honestly', async ({ page }) => {
  await setup(page);
  const first = page.locator('[data-subscription-quota="a@example.test"]');
  const second = page.locator('[data-subscription-quota="b@example.test"]');
  await expect(first).toContainText('67.5% 剩余');
  await expect(first).toContainText('82% 剩余');
  await expect(first).toContainText('重置');
  await expect(second.locator('progress.is-low')).toHaveAttribute('value', '8');
  await page.evaluate(() => {
    window.subscriptionUsageResult.accounts['a@example.test'].windows.push({ key:'seven_day_fable', label:'一周 Fable', remaining_percent:100 });
  });
  await page.locator('#subscriptionUsageRefresh').click();
  await expect(first.getByText('每周总额度', {exact:true})).toBeVisible();
  await expect(first.getByText('Fable 每周额度', {exact:true})).toBeVisible();
  for (const width of [1440, 390]) {
    await page.setViewportSize({width, height:900});
    expect(await first.locator('.subscription-quota-label').evaluateAll(nodes => nodes.every(n => n.scrollWidth <= n.clientWidth && n.scrollHeight <= n.clientHeight))).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  }
  await page.screenshot({path:'../artifacts/subscriptions-scoped-quota-mobile.png',fullPage:true});
  await page.evaluate(() => {
    window.subscriptionUsageResult.accounts['a@example.test'].status = 'rate_limited';
    window.subscriptionUsageResult.accounts['a@example.test'].stale = true;
    window.subscriptionUsageResult.accounts['a@example.test'].message = '官方查询限频，稍后自动重试。';
    window.subscriptionUsageResult.accounts['b@example.test'] = { status: 'auth_required', message: '账号授权已过期，请重新添加账号。', windows: [] };
  });
  await page.locator('#subscriptionUsageRefresh').click();
  await expect(first).toContainText('上次读数');
  await expect(first).toContainText('67.5% 剩余');
  await expect(second).toContainText('暂无数据');
  await expect(second).not.toContainText('100%');
  await expect(page.locator('[data-subscription-id="b@example.test"]')).toBeEnabled();
});

test('slow quota reads do not block switching or render into another page', async ({ page }) => {
  await setup(page);
  await expect(page.locator('#subscriptionUsageRefresh')).toBeEnabled();
  await page.evaluate(() => { window.subscriptionUsageWait = true; });
  await page.locator('#subscriptionUsageRefresh').click();
  await expect(page.locator('#subscriptionUsageRefresh')).toBeDisabled();
  await page.locator('[data-subscription-id="b@example.test"]').click();
  await expect(page.locator('.subscription-routing')).toContainText('b@example.test');
  await page.locator('[data-view="workbench"]').click();
  await page.evaluate(() => { window.resolveSubscriptionUsage(); });
  await expect(page.locator('#content')).not.toContainText('Usage limits');
});

test('manual quota failure preserves cached values without locking account actions', async ({ page }) => {
  await setup(page);
  await expect(page.locator('[data-subscription-quota="a@example.test"]')).toContainText('67.5% 剩余');
  await page.evaluate(() => { window.subscriptionUsageFail = true; });
  await page.locator('#subscriptionUsageRefresh').click();
  await expect(page.locator('[data-subscription-quota="a@example.test"]')).toContainText('上次读数');
  await expect(page.locator('[data-subscription-quota="a@example.test"]')).toContainText('67.5% 剩余');
  await expect(page.locator('[data-subscription-id="b@example.test"]')).toBeEnabled();
  await page.evaluate(() => { window.subscriptionStatus.native.last_switch = { phase: 'complete', history_transferred: false }; });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('.subscription-routing')).toContainText('旧版切换未共享会话，请重新切换');
});

test('a running daemon with stale OAuth is configured but never shown as signed in', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.subscriptionStatus.native.authenticated = false; });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('.subscription-account').first()).toContainText('登录已失效');
  await expect(page.locator('.subscription-account').first().getByRole('button', { name: '重新登录', exact: true })).toBeEnabled();
  await expect(page.locator('.subscription-account').first()).not.toContainText('Science 已登录');
});


test('compact hot rows refresh one account, protect native login and fit narrow screens', async ({ page }) => {
  await setup(page);
  await page.locator('#subscriptionHotEnable').click();
  await page.getByRole('button', {name:'刷新 b@example.test 的额度'}).click();
  await expect.poll(() => page.evaluate(() => window.lastUsageParams)).toEqual({id:'b@example.test',force:true});
  await page.getByLabel('管理账号 a@example.test').click();
  await expect(page.locator('[data-subscription-delete="a@example.test"]')).toBeDisabled();
  await page.getByLabel('管理账号 a@example.test').click();
  await page.setViewportSize({width:1440,height:900});
  await page.screenshot({path:'../artifacts/subscriptions-hot-desktop.png',fullPage:true});
  await page.locator('#themeBtn').click();
  await page.screenshot({path:'../artifacts/subscriptions-hot-dark.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.screenshot({path:'../artifacts/subscriptions-hot-mobile.png',fullPage:true});
});


test('priority can be turned off without losing automatic fallback', async ({ page }) => {
  await setup(page);
  await page.locator('#subscriptionHotEnable').click();
  const a = page.getByRole('switch', {name:'优先消耗 a@example.test'});
  await a.click();
  await expect(a).toBeChecked();
  await page.locator('#refreshBtn').click();
  await expect(a).toBeChecked();
  await a.click();
  await expect(a).not.toBeChecked();
  await expect(page.getByRole('switch', {name:'优先消耗 b@example.test'})).not.toBeChecked();
  await expect(page.locator('.subscription-policy')).toContainText('未指定优先账号');
  await expect(page.locator('.subscription-policy')).toContainText('自动使用备用账号');
  expect(await page.evaluate(() => window.subscriptionCalls.filter(c => c.operation === 'priority'))).toEqual([
    {operation:'priority',params:{id:'a@example.test',enabled:true}},
    {operation:'priority',params:{id:'a@example.test',enabled:false}}
  ]);
});

test('account menus dismiss outside, on Escape and when another opens', async ({ page }) => {
  await setup(page); await page.locator('#subscriptionHotEnable').click();
  const a = page.getByLabel('管理账号 a@example.test');
  const b = page.getByLabel('管理账号 b@example.test');
  await a.click();
  await expect(page.locator('.subscription-account-menu[open]')).toHaveCount(1);
  await page.locator('#pageTitle').click();
  await expect(page.locator('.subscription-account-menu[open]')).toHaveCount(0);
  await a.click(); await page.keyboard.press('Escape');
  await expect(page.locator('.subscription-account-menu[open]')).toHaveCount(0);
  await expect(a).toBeFocused();
  await a.click(); await b.click();
  await expect(a.locator('..')).not.toHaveAttribute('open');
  await expect(b.locator('..')).toHaveAttribute('open');
  await page.getByRole('button', {name:'重新授权',exact:true}).last().click();
  await expect(page.locator('.subscription-account-menu[open]')).toHaveCount(0);
});

test('usage refreshes only at startup and by explicit refresh, never on timers or focus', async ({ page }) => {
  await page.clock.install();
  await setup(page);
  await expect.poll(() => page.evaluate(() => window.subscriptionUsageCalls)).toBe(1);
  await page.locator('#subscriptionHotEnable').click();
  await page.getByRole('switch', {name:'优先消耗 a@example.test'}).click();
  await page.clock.fastForward(180000);
  await page.evaluate(() => { window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); });
  await page.locator('[data-view="workbench"]').click();
  await page.locator('[data-view="subscriptions"]').click();
  expect(await page.evaluate(() => window.subscriptionUsageCalls)).toBe(1);
  await page.locator('#subscriptionUsageRefresh').click();
  await expect.poll(() => page.evaluate(() => window.subscriptionUsageCalls)).toBe(2);
  expect(await page.evaluate(() => window.lastUsageParams)).toEqual({force:true});
  await page.locator('#refreshBtn').click();
  await expect.poll(() => page.evaluate(() => window.subscriptionUsageCalls)).toBe(3);
});

test('login identity is distinct from consumption and sync requires explicit restart confirmation', async ({ page }) => {
  await setup(page);
  await page.locator('#subscriptionHotEnable').click();
  await expect(page.locator('.subscription-login-identity')).toContainText('a@example.test');
  await expect(page.locator('.subscription-login-identity')).toContainText('与优先账号不同');
  await page.locator('#subscriptionSyncLogin').click();
  await expect(page.locator('#confirmBody')).toContainText('重启一次');
  await page.locator('#confirmDialog').getByRole('button',{name:'取消',exact:true}).click();
  expect(await page.evaluate(() => window.subscriptionCalls.some(c => c.operation==='sync_login'))).toBe(false);
  await page.locator('#subscriptionSyncLogin').click();
  await page.locator('#confirmDialog').getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.locator('.subscription-login-identity')).toContainText('b@example.test');
  await expect(page.locator('.subscription-login-identity')).toContainText('与优先账号一致');
  await expect(page.locator('#subscriptionSyncLogin')).toHaveCount(0);
  await expect(page.locator('#content')).toContainText('切换无需重启');
});
