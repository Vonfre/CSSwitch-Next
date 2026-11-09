import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function setup(page, accounts = true) {
  await page.addInitScript(({ accounts }) => {
    window.subscriptionCalls = [];
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
        window.subscriptionCalls.push(args);
        if (args.operation === 'status') return window.subscriptionStatus;
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
  await expect(page.locator('.subscription-policy')).toContainText('重启');
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
