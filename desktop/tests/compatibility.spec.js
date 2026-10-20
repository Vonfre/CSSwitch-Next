import { test, expect } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';

async function fixture(page, running = false) {
  await page.addInitScript(({ running }) => {
    window.testCalls = []; window.removedListeners = 0;
    window.compatReport = {
      checked_at: '2026-10-20T02:00:00Z', baseline: '0.1.67', science_version: '0.1.67', bundle_version: '0.1.25',
      runtime_version: '0.1.67', binary: '/Users/private/.claude-science/bin/claude-science', runtime_path: '/Users/private/runtime',
      available_profiles: 24, total_profiles: 24, status: 'pass',
      checks: [
        { id: 'binary', label: '实际启动版本', status: 'pass', detail: 'Claude Science 0.1.67', action: '' },
        { id: 'bundle', label: '自动更新版本', status: 'info', detail: '应用包 0.1.25；实际启动 0.1.67。', action: '' },
        { id: 'connectors', label: '连接器资源', status: 'pass', detail: '24/24 项资源可用。', action: '资源检查不代表已连接。' },
      ],
    };
    const config = { mode: 'proxy', provider: 'test', proxy_port: 18992, sandbox_port: 8991,
      connections: [{ id: 'test', name: '本机测试', protocol: 'anthropic', base_url: 'https://api.example.invalid', model: 'test-model', has_key: true, max_tokens: 8192 }] };
    const runtime = { proxy: running ? 'green' : 'amber', sandbox: running ? 'green' : 'amber' };
    window.__TAURI__ = {
      event: { listen: async (name, listener) => { window.progressListener = listener; return () => { window.progressListener = null; window.removedListeners++; }; } },
      core: { invoke: async (cmd) => {
        window.testCalls.push(cmd);
        if (cmd === 'get_config') return config;
        if (cmd === 'status') return runtime;
        if (cmd === 'app_version') return '0.8.3';
        if (cmd === 'check_update') return { available: false };
        if (cmd === 'diagnostics') return { version: '0.8.3', platform: 'aarch64', science_installed: true,
          compatibility: window.compatReport, compatibility_error: window.compatReport ? null : '检查未完成，请重试。' };
        if (cmd === 'one_click_login') {
          window.progressListener?.({ payload: { phase: 'connectors' } });
          await new Promise(resolve => { window.finishLaunch = resolve; });
          if (window.failLaunch) throw new Error('Science 启动失败，请查看日志。');
          runtime.proxy = runtime.sandbox = 'green';
          return { msg: 'Science 已就绪。' };
        }
        if (cmd === 'read_logs') return { proxy: '', sandbox: 'Science test log' };
      } },
    };
  }, { running });
}

test('shows actual and bundle versions, exports only safe fields, refreshes checks', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await expect(page.locator('#appVersion')).toHaveText('0.8.3');
  await page.locator('[data-view="settings"]').click();
  const card = page.locator('#compatibilityCard');
  await expect(card).toContainText('0.1.67'); await expect(card).toContainText('0.1.25');
  await expect(card).toContainText('环境正常');
  const pending = page.waitForEvent('download'); await page.locator('#exportCompatibility').click();
  const download = await pending; const report = readFileSync(await download.path(), 'utf8');
  expect(report).not.toContain('/Users/'); expect(JSON.parse(report)).not.toHaveProperty('binary');
  expect(JSON.parse(report).science_version).toBe('0.1.67');
  await page.evaluate(() => {
    window.compatReport.status = 'warning';
    window.compatReport.checks.push({ id: 'node', label: 'Ketcher 运行环境', status: 'warning', detail: '未找到 Node.js。', action: '安装 Node.js 22 或更新版本后重新检查。' });
  });
  await page.locator('#checkCompatibility').click();
  await expect(card).toContainText('需要关注'); await expect(card).toContainText('安装 Node.js 22');
  await expect(page.locator('.compat-issues')).toBeVisible();
  await expect(page.locator('.compat-details')).not.toHaveAttribute('open');
  expect(await page.evaluate(() => window.testCalls.some(c => ['one_click_login', 'stop_all', 'mcp_manage'].includes(c)))).toBe(false);
});

test('check failure has retry and successful recovery; narrow dark layout stays within viewport', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.evaluate(() => { window.savedReport = window.compatReport; window.compatReport = null; });
  await page.locator('[data-view="settings"]').click();
  await expect(page.locator('#compatibilityCard')).toContainText('检查未完成，请重试');
  await page.evaluate(() => { window.compatReport = window.savedReport; });
  await page.locator('#checkCompatibility').click();
  await expect(page.locator('#compatibilityCard')).toContainText('环境正常');
  await page.getByRole('button', { name: '关闭提示', exact: true }).click();
  mkdirSync('../artifacts', { recursive: true });
  await page.screenshot({ path: '../artifacts/compatibility-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 }); await page.locator('#themeBtn').click();
  await page.locator('.compat-details > summary').click();
  await page.locator('.compat-paths summary').click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../artifacts/compatibility-mobile-dark.png', fullPage: true });
});

test('settings hide secondary information, contextual help supports keyboard and escapes markup', async ({ page }) => {
  await fixture(page); await page.goto('/'); await page.locator('[data-view="settings"]').click();
  await expect(page.locator('.compat-checks')).not.toBeVisible();
  await expect(page.locator('#proxyPort')).not.toBeVisible();
  await expect(page.getByRole('switch', { name: '使用本机 MCP' })).toHaveCount(0);
  const button = page.getByRole('button', { name: '页面汉化说明', exact: true });
  await button.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('tooltip')).toContainText('对话、代码和输入内容保持原文');
  await expect(button).toHaveAttribute('aria-describedby', 'helpPopover');
  await page.keyboard.press('Escape'); await expect(page.getByRole('tooltip')).not.toBeVisible();
  await button.evaluate(el => { el.dataset.help = '<img src=x onerror="window.helpXss=true">'; });
  await button.click(); await expect(page.getByRole('tooltip')).toContainText('<img');
  await expect(page.locator('#helpPopover img')).toHaveCount(0);
  await page.locator('[data-view="workbench"]').click(); await expect(page.locator('#helpPopover')).toBeHidden();
});

test('appearance persists and follows system changes only when selected', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await fixture(page); await page.goto('/'); await page.locator('[data-view="settings"]').click();
  await page.locator('#themePreference').selectOption('dark');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload(); await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.locator('[data-view="settings"]').click();
  await page.locator('#themePreference').selectOption('system');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: '../artifacts/settings-redesign-dark.png', fullPage: true });
  await page.locator('#themeBtn').click();
  await expect(page.locator('#themePreference')).toHaveValue('light');
});

test('transient success notices dismiss automatically and never follow navigation', async ({ page }) => {
  await fixture(page); await page.goto('/'); await page.locator('[data-view="settings"]').click();
  await page.locator('#checkCompatibility').click(); await expect(page.locator('#notice')).toBeVisible();
  await page.locator('[data-view="workbench"]').click(); await expect(page.locator('#notice')).toBeHidden();
  await page.locator('[data-view="settings"]').click();
  await page.clock.install(); await page.locator('#checkCompatibility').click();
  await expect(page.locator('#notice')).toBeVisible();
  await page.clock.fastForward(5100); await expect(page.locator('#notice')).toBeHidden();
});

test('startup displays native phases, prevents duplicate launch and cleans up on failure', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.locator('#startBtn').click(); await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#launchPhase')).toHaveText('正在接入本机连接器');
  await expect(page.locator('#launchElapsed')).toContainText('秒');
  expect(await page.locator('#content').evaluate(el => el.inert)).toBe(true);
  await page.evaluate(() => { window.failLaunch = true; window.finishLaunch(); });
  await expect(page.locator('#launchProgress')).toBeHidden();
  await expect(page.locator('#launchHelp')).toBeVisible();
  await expect(page.locator('#notice')).toContainText('Science 启动失败');
  expect(await page.evaluate(() => window.removedListeners)).toBe(1);
  await page.locator('#launchDiagnostics').click();
  await expect(page.locator('#compatibilityCard')).toBeVisible();
  expect(await page.evaluate(() => window.testCalls.filter(c => c === 'one_click_login').length)).toBe(1);
});

test('success removes progress and reopening a live session uses only the open command', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.locator('#startBtn').click(); await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#launchProgress')).toBeVisible();
  await page.evaluate(() => window.finishLaunch());
  await expect(page.locator('#startBtn')).toHaveText('打开 Science');
  await page.locator('#startBtn').click();
  await expect(page.locator('#notice')).toContainText('已打开正在运行的 Science');
  expect(await page.evaluate(() => window.testCalls.filter(c => c === 'one_click_login').length)).toBe(1);
  expect(await page.evaluate(() => window.testCalls.filter(c => c === 'open_url').length)).toBe(1);
  await expect(page.locator('#confirmDialog')).not.toBeVisible();
  await expect(page.locator('#launchProgress')).toBeHidden();
});


test('font preference applies across navigation, persists, and preserves monospace logs', async ({ page }) => {
  await fixture(page); await page.goto('/'); await page.locator('[data-view="settings"]').click();
  await expect(page.locator('#fontPreference')).toHaveValue('system');
  for (const [id, family] of [['pingfang', 'PingFang SC'], ['helvetica', 'Helvetica Neue'], ['songti', 'Songti SC']]) {
    await page.locator('#fontPreference').selectOption(id);
    await expect(page.locator('body')).toHaveCSS('font-family', new RegExp(family));
    await expect(page.locator('#fontPreference')).toHaveCSS('font-family', new RegExp(family));
    await expect(page.locator('.nav-item').first()).toHaveCSS('font-family', new RegExp(family));
  }
  await page.locator('[data-view="logs"]').click();
  await expect(page.locator('#logText')).toHaveCSS('font-family', /monospace/);
  await page.reload(); await expect(page.locator('body')).toHaveCSS('font-family', /Songti SC/);
  await page.locator('[data-view="settings"]').click();
  await expect(page.locator('#fontPreference')).toHaveValue('songti');
  await page.locator('#fontPreference').selectOption('system');
  await page.reload(); await expect(page.locator('html')).toHaveAttribute('data-font', 'system');
  expect(await page.evaluate(() => window.testCalls.some(c => ['stop_all', 'set_config', 'set_science_features'].includes(c)))).toBe(false);
});

test('unknown saved fonts fall back safely and settings fit each font at narrow widths', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.evaluate(() => localStorage.setItem('csswitch-font', 'unknown-font'));
  await page.reload(); await expect(page.locator('html')).toHaveAttribute('data-font', 'system');
  await page.locator('[data-view="settings"]').click();
  await expect(page.locator('#fontPreference')).toHaveValue('system');
  for (const width of [390, 680, 980]) {
    await page.setViewportSize({ width, height: 860 });
    for (const id of ['system', 'pingfang', 'helvetica', 'songti']) {
      await page.locator('#fontPreference').selectOption(id);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      const select = await page.locator('#fontPreference').boundingBox();
      expect(select.x + select.width).toBeLessThanOrEqual(width);
    }
    if (width === 390) await page.screenshot({ path: '../artifacts/settings-font-mobile.png', fullPage: true });
  }
  await page.locator('#fontPreference').selectOption('system');
  await page.screenshot({ path: '../artifacts/settings-0.8.3-light.png', fullPage: true });
  await page.locator('#themePreference').selectOption('dark');
  await expect(page.locator('#checkUpdateBtn')).toHaveCSS('background-color', 'rgb(41, 41, 41)');
  await page.screenshot({ path: '../artifacts/settings-0.8.3-dark.png', fullPage: true, animations: 'disabled' });
});
