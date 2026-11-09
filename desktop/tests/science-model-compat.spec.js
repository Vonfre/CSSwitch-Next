import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';

test('legacy preferences are normalized before native modules without adding menu items', async ({ page }) => {
  const script = readFileSync(new URL('../../scripts/science-model-compat.js', import.meta.url), 'utf8')
    .replace('__CSSWITCH_NATIVE_IDS__', JSON.stringify({ opus: 'claude-opus-5', sonnet: 'claude-sonnet-5' }));
  await page.addInitScript(() => {
    localStorage.setItem('operon-default-model', 'claude-sonnet-99-20-20687152');
    localStorage.setItem('operon-subagent-model', 'claude-opus-99-01-20123456');
    localStorage.setItem('csswitch-science-default-model', 'retired-override');
    localStorage.setItem('unrelated', 'keep-this');
  });
  await page.route('**/__csswitch/model-compat.js', route => route.fulfill({ contentType: 'text/javascript', body: script }));
  await page.route('**/model-fixture', route => route.fulfill({ contentType: 'text/html', body: '<html><head><script type="module">window.nativeDefault=localStorage.getItem("operon-default-model")</script></head><body><script src="/__csswitch/model-compat.js"></script></body></html>' }));
  await page.goto('/model-fixture');
  expect(await page.evaluate(() => window.nativeDefault)).toBe('claude-sonnet-5');
  expect(await page.evaluate(() => localStorage.getItem('operon-subagent-model'))).toBe('claude-opus-5');
  expect(await page.evaluate(() => localStorage.getItem('csswitch-science-default-model'))).toBeNull();
  expect(await page.evaluate(() => localStorage.getItem('unrelated'))).toBe('keep-this');
  await expect(page.locator('button, [role="menu"], [role="option"]')).toHaveCount(0);
});


test('retired Haiku preferences use the native default family', async ({ page }) => {
  const script = readFileSync(new URL('../../scripts/science-model-compat.js', import.meta.url), 'utf8')
    .replace('__CSSWITCH_NATIVE_IDS__', JSON.stringify({ opus: 'claude-opus-5', sonnet: 'claude-sonnet-5' }));
  await page.addInitScript(() => localStorage.setItem('operon-default-model', 'claude-haiku-99-01-20123456'));
  await page.route('**/retired-model-fixture', route => route.fulfill({ contentType: 'text/html', body: '<html><body></body></html>' }));
  await page.goto('/retired-model-fixture');
  await page.addScriptTag({ content: script });
  expect(await page.evaluate(() => localStorage.getItem('operon-default-model'))).toBe('claude-sonnet-5');
});

test('four-slot mode normalizes old version preferences while keeping Haiku independent', async ({ page }) => {
  const script = readFileSync(new URL('../../scripts/science-model-compat.js', import.meta.url), 'utf8')
    .replace('__CSSWITCH_NATIVE_IDS__', JSON.stringify({ _four_slots: true,
      fable: 'claude-opus-99-01-20990101', opus: 'claude-opus-5', sonnet: 'claude-sonnet-5', haiku: 'claude-haiku-4-5' }));
  await page.goto('/');
  await page.evaluate(() => {
    localStorage.setItem('operon-default-model', 'claude-sonnet-4-6');
    localStorage.setItem('operon-subagent-model', 'claude-haiku-5-5');
  });
  await page.addScriptTag({ content: script });
  expect(await page.evaluate(() => localStorage.getItem('operon-default-model'))).toBe('claude-sonnet-5');
  expect(await page.evaluate(() => localStorage.getItem('operon-subagent-model'))).toBe('claude-haiku-4-5');
  await page.evaluate(() => localStorage.setItem('operon-default-model', 'claude-opus-99-01-20990101'));
  await page.addScriptTag({ content: script });
  expect(await page.evaluate(() => localStorage.getItem('operon-default-model'))).toBe('claude-opus-99-01-20990101');
});
