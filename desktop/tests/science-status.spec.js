import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

const script = readFileSync(new URL('../../scripts/science-status.js', import.meta.url), 'utf8');
for (const enabled of [true, false]) {
  test(`retry attribution follows third-party mode (${enabled}) without adding model UI`, async ({ page }) => {
    await page.route('**/__csswitch/status/config', route => route.fulfill({ json: { enabled } }));
    await page.goto('/');
    await page.setContent('<div role="status">Claude is temporarily unavailable — retrying<span> · </span><a href="https://status.claude.com/">Check service status</a></div><article>Claude is temporarily unavailable — retrying</article>');
    const ready = page.waitForResponse('**/__csswitch/status/config');
    await page.addScriptTag({ content: script });
    await ready;
    const status = page.getByRole('status');
    if (enabled) {
      await expect(status).toContainText('当前 API 服务商暂时不可用');
      await expect(status.locator('a')).toBeHidden();
    } else {
      await expect(status).toContainText('Claude is temporarily unavailable');
      await expect(status.locator('a')).toBeVisible();
    }
    await expect(page.locator('article')).toHaveText('Claude is temporarily unavailable — retrying');
    await expect(page.locator('[data-csswitch-model-menu], [data-csswitch-model-settings]')).toHaveCount(0);
  });
}
