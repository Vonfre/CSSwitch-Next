import { test, expect } from '@playwright/test';

async function setup(page, mode = 'available') {
  await page.addInitScript((mode) => {
    window.updateMode = mode; window.updateCalls = []; window.proxyRunning = false;
    window.__TAURI__ = { core: { Channel: class {}, invoke: async (cmd, args) => {
      window.updateCalls.push(cmd);
      if (cmd === 'get_config') return { provider: '', connections: [], mode: 'proxy', proxy_port: 18992, sandbox_port: 8991 };
      if (cmd === 'status') {
        if (window.runtimeFailure) throw new Error('读取运行状态失败，请重试');
        return { proxy: window.proxyRunning ? 'green' : 'amber', sandbox: 'amber' };
      }
      if (cmd === 'app_version') return '0.5.0';
      if (cmd === 'diagnostics') return { version: '0.5.0', platform: 'macOS' };
      if (cmd === 'check_update') {
        if (window.updateMode === 'offline') throw new Error('无法检查更新：网络不可用');
        return { current: '0.5.0', available: window.updateMode !== 'latest', version: '0.6.0', notes: '<script>untrusted release</script>', downloaded: false };
      }
      if (cmd === 'download_update') {
        args.onProgress.onmessage({ downloaded: 512, total: 1024 });
        await new Promise(r => setTimeout(r, 250));
        if (window.downloadFailure) throw new Error('签名验证失败，未安装更新');
        return { available: true, downloaded: true, version: '0.6.0' };
      }
      if (cmd === 'install_update' && window.installFailure) throw new Error('安装失败，可重试');
    } } };
  }, mode);
  await page.goto('/');
  await page.getByRole('button', { name: '设置', exact: true }).click();
}

test('startup detects a release without installation; download progress, signature failure and retry', async ({ page }) => {
  await setup(page);
  await expect(page.locator('#updateBadge')).toContainText('0.6.0');
  expect(await page.evaluate(() => window.updateCalls.includes('install_update'))).toBe(false);
  await page.getByText('新版说明', { exact: true }).click();
  await expect(page.locator('.update-notes')).toContainText('<script>');
  expect(await page.locator('.update-notes script').count()).toBe(0);
  await page.evaluate(() => { window.downloadFailure = true; });
  await page.getByRole('button', { name: '下载更新', exact: true }).click();
  await expect(page.getByRole('progressbar')).toHaveAttribute('value', '50');
  await expect(page.locator('#updateCard')).toContainText('签名验证失败');
  await expect(page.getByRole('button', { name: '安装并重启' })).toHaveCount(0);
  await page.evaluate(() => { window.downloadFailure = false; });
  await page.getByRole('button', { name: '下载更新', exact: true }).click();
  await expect(page.getByRole('button', { name: '安装并重启' })).toBeVisible();
});

test('running gateway blocks installation; cancel and install failure keep the verified download', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: '下载更新', exact: true }).click();
  await expect(page.getByRole('button', { name: '安装并重启' })).toBeVisible();
  await page.evaluate(() => { window.runtimeFailure = true; });
  await page.getByRole('button', { name: '安装并重启' }).click();
  await expect(page.locator('#updateCard')).toContainText('读取运行状态失败');
  await page.evaluate(() => { window.runtimeFailure = false; });
  await page.evaluate(() => { window.proxyRunning = true; });
  await page.getByRole('button', { name: '安装并重启' }).click();
  await expect(page.locator('#updateCard')).toContainText('请先在提供商页面停止');
  expect(await page.evaluate(() => window.updateCalls.includes('install_update'))).toBe(false);
  await page.evaluate(() => { window.proxyRunning = false; window.installFailure = true; });
  await page.getByRole('button', { name: '安装并重启' }).click();
  await page.getByRole('button', { name: '取消', exact: true }).click();
  expect(await page.evaluate(() => window.updateCalls.includes('install_update'))).toBe(false);
  await page.getByRole('button', { name: '安装并重启' }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#updateCard')).toContainText('安装失败，可重试');
  await expect(page.getByRole('button', { name: '安装并重启' })).toBeEnabled();
  await page.evaluate(() => { window.installFailure = false; });
  await page.getByRole('button', { name: '安装并重启' }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#updateCard')).toContainText('即将重启');
});

test('offline auto-check stays quiet and manual retry reaches latest state', async ({ page }) => {
  await setup(page, 'offline');
  await expect(page.locator('#updateCard')).toContainText('网络不可用');
  await expect(page.locator('#notice')).toBeHidden();
  await expect(page.locator('#updateBadge')).toBeHidden();
  await page.evaluate(() => { window.updateMode = 'latest'; });
  await page.getByRole('button', { name: '检查更新', exact: true }).click();
  await expect(page.locator('#updateCard')).toContainText('当前已是最新版本');
  await expect(page.locator('#updateCard [role="alert"]')).toHaveCount(0);
});

test('download survives navigating away and update card fits a narrow window', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 }); await setup(page);
  await page.getByRole('button', { name: '下载更新', exact: true }).click();
  await page.getByRole('button', { name: '提供商', exact: true }).click();
  await expect(page.locator('#pageTitle')).toHaveText('提供商');
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await expect(page.getByRole('button', { name: '安装并重启' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: '../artifacts/updater-mobile.png', fullPage: true });
});
