import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
const plugin = readFileSync(new URL('../../scripts/science-zh.js', import.meta.url), 'utf8');

test('Science UI translation restores original UI and leaves research content intact', async ({ page }) => {
  await page.route('**/__csswitch/config', route => route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<nav><button title="New session">New session</button></nav><section role="dialog"><h2>Settings</h2><p class="markdown">Settings</p><div class="markdown-content"><h3>Research</h3><button>Save</button></div><pre>Save</pre><code>Delete</code></section><input placeholder="Search" value="Save"><textarea placeholder="Type a message...">Settings</textarea><div contenteditable="true">New session</div><p data-message-content>Settings</p><label>Name</label>');
  await page.addScriptTag({content:plugin});
  await expect(page.locator('nav button')).toHaveText('新建会话');
  await expect(page.locator('nav button')).toHaveAttribute('title','新建会话');
  await expect(page.locator('h2')).toHaveText('设置');
  await expect(page.locator('.markdown')).toHaveText('Settings');
  await expect(page.locator('.markdown-content h3')).toHaveText('Research');
  await expect(page.locator('.markdown-content button')).toHaveText('Save');
  await expect(page.locator('pre')).toHaveText('Save');
  await expect(page.locator('code')).toHaveText('Delete');
  await expect(page.locator('input')).toHaveValue('Save');
  await expect(page.locator('input')).toHaveAttribute('placeholder','搜索');
  await expect(page.locator('textarea')).toHaveValue('Settings');
  await expect(page.locator('[contenteditable]')).toHaveText('New session');
  await expect(page.locator('[data-message-content]')).toHaveText('Settings');
  await page.evaluate(() => window.__csswitchZh.setEnabled(false));
  await expect(page.locator('nav button')).toHaveText('New session');
  await expect(page.locator('h2')).toHaveText('Settings');
  await expect(page.locator('input')).toHaveAttribute('placeholder','Search');
});

test('dynamic menus translate and framework changes win over stale translations', async ({ page }) => {
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/__csswitch/config', route => route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<button>Save</button>');
  await page.addScriptTag({content:plugin});
  await expect(page.locator('button')).toHaveText('保存');
  await page.evaluate(() => { const menu=document.createElement('div'); menu.setAttribute('role','menu'); menu.innerHTML='<button>Cancel</button>';document.body.append(menu); });
  await expect(page.getByRole('menu').getByRole('button')).toHaveText('取消');
  await page.evaluate(() => document.querySelector('button').firstChild.nodeValue='Unknown UI copy');
  await expect(page.locator('button').first()).toHaveText('Unknown UI copy');
  await page.evaluate(() => window.__csswitchZh.setEnabled(false));
  await expect(page.locator('button').first()).toHaveText('Unknown UI copy');
  await expect(page.getByRole('menu').getByRole('button')).toHaveText('Cancel');
  expect(errors).toEqual([]);
});
