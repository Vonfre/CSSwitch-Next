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
