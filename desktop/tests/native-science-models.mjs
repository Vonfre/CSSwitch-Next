import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';
const { url, root, config, gateway_config, expected_models } = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(url);
  const signIn = page.locator('form[action*="/api/auth/nonce"] button');
  if (await signIn.count()) await signIn.click();
  const catalog = await (await page.request.get(new URL('/api/models', page.url()).href)).json();
  assert.deepEqual(catalog.models.anthropic.map(m => m.id), expected_models.map(m => m.id));
  assert.deepEqual(catalog.models.anthropic.map(m => Boolean(m.overflow)), expected_models.map(m => Boolean(m.overflow)));
  assert.ok(catalog.models.anthropic.every(model => /^Claude (Opus|Sonnet|Haiku) /.test(model.name) && model.name.includes(' → ')));
  const expected = id => expected_models.find(m => m.id === id);
  for (const m of catalog.models.anthropic) assert.ok(m.name.startsWith(expected(m.id).name + ' → '));
  assert.equal(await page.locator('script[src*="/__csswitch/models"]').count(), 0);
  assert.equal((await page.request.get(new URL('/__csswitch/models/config', page.url()).href)).status(), 410);
  if (process.argv.includes('--collect')) {
    writeFileSync(root + '/native-models.json', JSON.stringify(catalog, null, 2));
  } else {
    const before = readFileSync(config, 'utf8');
    const openSettings = async () => {
      await page.getByRole('button', { name: /^(Account menu|账号菜单)/ }).click();
      await page.getByRole('menuitem', { name: /^(Settings|设置)$/ }).click();
      await page.getByTestId('settings-tab-general').click();
    };
    const modal = page.getByTestId('settings-modal');
    for (let i = 0; i < 3; i++) {
      if (!(await modal.isVisible())) await openSettings();
      const models = modal.locator('button[role="combobox"][aria-label^="Model:"]');
      await models.nth(i).click();
      await page.getByRole('option').first().waitFor();
      assert.equal(await page.locator('[data-csswitch-model-settings], [data-csswitch-model-menu], .cs-model-secondary').count(), 0);
      assert.ok((await page.getByRole('option').allTextContents()).some(text => text.includes(' → fixture-')));
      await page.keyboard.press('Escape');
      // Native Escape may also animate the settings sheet closed.
      await page.waitForTimeout(400);
    }
    if (await modal.isVisible()) await page.getByRole('button', { name: /^(Close settings|关闭设置)$/ }).click();
    assert.equal(readFileSync(config, 'utf8'), before);
    const project = page.getByRole('button', { name: 'Open project Example project', exact: true });
    if (await project.count()) await project.click();
    const pick = async family => {
      await page.locator('button[aria-label^="Model:"]:visible').click();
      const popup = page.getByTestId('model-selector-popup');
      const name = catalog.models.anthropic.find(model => model.name.includes(family)).name.replace(/^Claude /, '');
      await popup.getByText(name, { exact: true }).click();
      assert.equal(await page.locator('.cs-model-secondary').count(), 0);
    };
    const send = async text => {
      const editor = page.locator('[contenteditable="true"]:visible').first();
      await editor.fill(text); await editor.press('Enter');
      await page.waitForTimeout(5000);
    };
    await page.getByTestId('new-session-button').click();
    await pick('Opus');
    await send('Reply OK. Quality mapping verification.');
    await page.getByTestId('new-session-button').click();
    await pick('Sonnet');
    await send('Reply OK. Default mapping verification.');
    // Change the saved desktop mapping while the native daemon stays running.
    const next = JSON.parse(before);
    next.providers.fixture.quality_model = 'updated-quality';
    writeFileSync(config, JSON.stringify(next)); writeFileSync(gateway_config, JSON.stringify(next));
    await page.getByRole('button', { name: 'Reply OK. Quality mapping verification.', exact: true }).click();
    await send('Reply OK. Updated quality mapping verification.');
    const after = await (await page.request.get(new URL('/api/models', page.url()).href)).json();
    assert.deepEqual(after.models.anthropic.map(model => model.id), catalog.models.anthropic.map(model => model.id));
    assert.ok(after.models.anthropic.filter(model => model.id.includes('opus')).every(model => model.name.endsWith(' → updated-quality')));
    assert.equal(errors.length, 0, errors.join(';'));
    console.log('PASS: native settings and composer preserved without custom model UI; exact original catalog and original → mapped labels; quality/default roles use separate protocols; existing conversation follows updated CSSwitch mapping.');
  }
} finally { await browser.close(); }
