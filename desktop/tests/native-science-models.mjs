import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const { url, root, config, gateway_config } = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.goto(url);
  const signIn = page.locator('form[action*="/api/auth/nonce"] button');
  if (await signIn.count()) await signIn.click();
  const openSettings = async () => {
    await page.getByRole('button', { name: /^Account menu/ }).click();
    await page.getByRole('menuitem', { name: /^Settings$/ }).click();
    await page.getByTestId('settings-tab-general').click();
  };
  await openSettings();
  const catalog = await (await page.request.get(new URL('/api/models', page.url()).href)).json();
  const listed = catalog.models.anthropic;
  assert.equal(listed.length, 3);
  assert.ok(listed.some(m => m.id === catalog.default_model_id));
  assert.ok(listed.some(m => /fixture-quality/.test(m.name)));
  assert.equal(new Set(listed.map(m => m.name)).size, 3);
  const reviewer = await (await page.request.get(new URL('/api/preferences/reviewer-model', page.url()).href)).json();
  assert.ok(listed.some(m => m.id === reviewer.model));
  writeFileSync(root + '/models-dom.txt', await page.locator('body').innerText());
  console.log('Model controls:', await page.getByRole('combobox').evaluateAll(nodes => nodes.map(n => ({text:n.textContent,label:n.getAttribute('aria-label')}))));
  await page.screenshot({ path: root + '/models-before.png' });
  await page.getByRole('combobox').filter({ hasText: /fixture-quality/ }).first().click();
  await page.getByRole('option', { name: /fixture-quality/ }).waitFor();
  assert.equal(await page.getByRole('option', { name: /fixture-default/ }).count(), 2);
  await page.keyboard.press('Escape');
  assert.ok(!(await page.locator('body').innerText()).includes('(unavailable)'));
  // The native daemon caches /v1/models. Change both local files but keep the
  // daemon and gateway running: the page bridge must serve current labels.
  const next = JSON.parse(readFileSync(config, 'utf8'));
  next.providers.fixture.quality_model = 'updated-quality';
  next.providers.fixture.model = 'updated-default';
  writeFileSync(config, JSON.stringify(next));
  writeFileSync(gateway_config, JSON.stringify(next));
  const updated = await (await page.request.get(new URL('/api/models', page.url()).href)).json();
  assert.ok(updated.models.anthropic.some(m => /updated-quality/.test(m.name)));
  assert.equal(updated.default_model_id, catalog.default_model_id);
  await page.reload(); await openSettings();
  await page.getByRole('combobox').filter({ hasText: /updated-quality/ }).first().waitFor();
  assert.ok(!(await page.locator('body').innerText()).includes('(unavailable)'));
  await page.screenshot({ path: root + '/models-fixed.png' });
  assert.equal(errors.length, 0, errors.join(';'));
  console.log('PASS: native default, reviewer and three distinct model roles; live config refresh with unchanged daemon and zero browser errors.');
} finally { await browser.close(); }
