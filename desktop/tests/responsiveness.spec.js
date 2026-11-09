import { test, expect } from '@playwright/test';

async function setup(page, mode = 'proxy', slowStatus = false) {
  await page.addInitScript(({ mode, slowStatus }) => {
    window.calls = [];
    window.pending = {};
    window.hold = slowStatus ? { status: true } : {};
    const config = { mode, provider: '', connections: [], proxy_port: 18992, sandbox_port: 8991 };
    window.__TAURI__ = { core: { invoke: async (cmd, args = {}) => {
      const key = cmd === 'subscription_manage' ? `subscription:${args.operation}` : cmd;
      window.calls.push(key);
      if (window.hold[key]) await new Promise((resolve, reject) => { window.pending[key] = { resolve, reject }; });
      if (cmd === 'get_config') return config;
      if (cmd === 'status') return { proxy: 'amber', sandbox: 'amber' };
      if (cmd === 'read_logs') return { proxy: 'Ready', sandbox: '' };
      if (cmd === 'check_update') return { available: false };
      if (cmd === 'app_version') return '0.8.18';
      if (cmd === 'subscription_manage') {
        if (args.operation === 'usage') return { accounts: {} };
        if (args.operation !== 'status') return { message: '完成' };
        return { accounts: [{ id: 'test', user: 'test@example.test', plan: 'pro' }],
          ready_accounts: 1, installed: true, science: { running: true }, native: {} };
      }
      throw new Error(`Unexpected command ${cmd}`);
    } } };
  }, { mode, slowStatus });
  await page.goto('/');
  await expect(page.locator('#search')).toBeVisible();
}

const navigate = (page, view) => page.locator(`button[data-view="${view}"]`).click();
const release = (page, key, fail = false) => page.evaluate(({ key, fail }) => {
  window.hold[key] = false;
  window.pending[key][fail ? 'reject' : 'resolve'](fail ? new Error('旧页面失败') : undefined);
}, { key, fail });

test('startup renders immediately while a status probe is pending', async ({ page }) => {
  await setup(page, 'proxy', true);
  await navigate(page, 'logs');
  await expect(page.locator('#logText')).toHaveText('Ready');
  await release(page, 'status');
  await expect(page.locator('#pageTitle')).toHaveText('运行日志');
  await expect(page.locator('#logText')).toHaveText('Ready');
});

test('slow page reads allow navigation and share requests when revisited', async ({ page }) => {
  await setup(page);
  // Startup now loads the accounts once for its quota read. Count only the
  // pending page request when checking that repeated navigation coalesces it.
  await expect.poll(() => page.evaluate(() => window.calls.includes('subscription:usage'))).toBe(true);
  await page.evaluate(() => { window.calls = []; window.hold['subscription:status'] = true; });
  await navigate(page, 'subscriptions');
  await expect(page.locator('#content')).toHaveAttribute('aria-busy', 'true');
  await navigate(page, 'workbench');
  await expect(page.locator('#search')).toBeVisible();
  await navigate(page, 'subscriptions');
  expect(await page.evaluate(() => window.calls.filter(c => c === 'subscription:status').length)).toBe(1);
  await navigate(page, 'logs');
  await release(page, 'subscription:status');
  await expect(page.locator('#logText')).toHaveText('Ready');
  await expect(page.locator('#pageTitle')).toHaveText('运行日志');
  await expect(page.locator('.subscription-panel')).toHaveCount(0);
});

test('late read failures do not show errors over the new page', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.hold['subscription:status'] = true; });
  await navigate(page, 'subscriptions');
  await navigate(page, 'workbench');
  await release(page, 'subscription:status', true);
  await expect(page.locator('#search')).toBeVisible();
  await expect(page.locator('#notice')).toBeHidden();
});

test('subscription refresh does not reload unrelated config or runtime', async ({ page }) => {
  await setup(page);
  await navigate(page, 'subscriptions');
  await expect(page.locator('.subscription-panel')).toBeVisible();
  await page.evaluate(() => { window.calls = []; });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('.subscription-panel')).toBeVisible();
  expect(await page.evaluate(() => window.calls)).toEqual(['subscription:status', 'subscription:usage']);
});

test('concurrent probes are coalesced and unchanged status preserves DOM and focus', async ({ page }) => {
  await setup(page);
  await page.locator('#search').fill('保持搜索');
  await page.evaluate(() => {
    window.savedRuntime = document.querySelector('#startBtn');
    window.savedIcon = document.querySelector('#themeBtn svg');
    window.calls = []; window.hold.status = true;
    window.probes = Promise.all([refreshRuntime(), refreshRuntime(), refreshRuntime()]);
    notice('正在检查');
  });
  expect(await page.evaluate(() => window.calls.filter(c => c === 'status').length)).toBe(1);
  await release(page, 'status');
  await page.evaluate(() => window.probes);
  expect(await page.evaluate(() => window.savedRuntime === document.querySelector('#startBtn'))).toBe(true);
  expect(await page.evaluate(() => window.savedIcon === document.querySelector('#themeBtn svg'))).toBe(true);
  await expect(page.locator('#search')).toBeFocused();
  await expect(page.locator('#search')).toHaveValue('保持搜索');
});

test('official mode does not poll the unused gateway', async ({ page }) => {
  await page.clock.install();
  await setup(page, 'official');
  await page.clock.fastForward(25000);
  expect(await page.evaluate(() => window.calls.filter(c => c === 'status'))).toEqual([]);
});

test('account mutations still prevent navigation until completion', async ({ page }) => {
  await setup(page);
  await navigate(page, 'subscriptions');
  await page.evaluate(() => { window.hold['subscription:switch'] = true; });
  await page.locator('[data-subscription-id="test"]').click();
  await expect(page.locator('#content')).toHaveAttribute('inert', '');
  await page.locator('button[data-view="workbench"]').evaluate(node => node.click());
  await expect(page.locator('body')).toHaveAttribute('data-view', 'subscriptions');
  await release(page, 'subscription:switch');
  await expect(page.locator('#content')).not.toHaveAttribute('inert', '');
  await navigate(page, 'workbench');
  await expect(page.locator('#search')).toBeVisible();
});


test('post-mutation refresh waits for a fresh probe instead of reusing old status', async ({ page }) => {
  await setup(page);
  await page.evaluate(async () => {
    await refreshRuntime();
    const invoke = window.__TAURI__.core.invoke;
    window.probeCount = 0;
    window.__TAURI__.core.invoke = (cmd, args) => {
      if (cmd !== 'status') return invoke(cmd, args);
      window.probeCount++;
      if (window.probeCount === 1) return new Promise(resolve => { window.releaseOldProbe = () => resolve({ proxy: 'amber', sandbox: 'amber' }); });
      return Promise.resolve({ proxy: 'green', sandbox: 'green' });
    };
    window.oldProbe = refreshRuntime();
    window.freshProbe = refreshRuntime(true);
  });
  expect(await page.evaluate(() => window.probeCount)).toBe(1);
  await page.evaluate(async () => { window.releaseOldProbe(); await window.freshProbe; });
  expect(await page.evaluate(() => window.probeCount)).toBe(2);
  await expect(page.locator('#startBtn')).toHaveText('打开 Science');
});
