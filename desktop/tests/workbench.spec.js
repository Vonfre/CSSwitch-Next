import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function fixture(page) {
  await page.addInitScript(() => {
    const configs = [{ id: 'deepseek', name: 'DeepSeek', protocol: 'anthropic', base_url: 'https://api.deepseek.com/anthropic', model: 'deepseek-v4-pro', quality_model: '', fast_model: 'deepseek-v4-flash', max_tokens: 8192, has_key: false, key_masked: '', builtin: true },
      { id: 'qwen', name: '通义千问', protocol: 'openai', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus', quality_model: '', fast_model: 'qwen-turbo', max_tokens: 8192, has_key: false, key_masked: '', builtin: true },
      { id: 'gpt', name: 'GPT', protocol: 'responses', base_url: 'https://api.example.com/v1', model: 'gpt-6.1-sol', quality_model: '', fast_model: '', max_tokens: 8192, has_key: true, builtin: false },
      { id: 'openrouter', name: 'OpenRouter', protocol: 'openai', base_url: 'https://openrouter.ai/api/v1', model: 'nvidia/nemotron-3-ultra-550b-a55b:free', quality_model: '', fast_model: '', max_tokens: 8192, has_key: true, builtin: false }];
    let config = { mode: 'proxy', provider: 'deepseek', proxy_port: 18992, sandbox_port: 8991, connections: configs };
    let runtime = { proxy: 'amber', sandbox: 'amber' };
    window.testCalls = [];
    window.__TAURI__ = { core: { invoke: async (cmd, args = {}) => {
      window.testCalls.push({ cmd, args });
      if (cmd === 'get_config') return structuredClone(config);
      if (cmd === 'status') return runtime;
      if (cmd === 'save_connection') {
        const i = config.connections.findIndex((p) => p.id === args.input.id);
        const p = { ...args.input, has_key: Boolean(args.input.key || config.connections[i]?.has_key), key_masked: '••••1234', builtin: config.connections[i]?.builtin || false };
        delete p.key;
        if (i === -1) config.connections.push(p); else config.connections[i] = p;
      }
      if (cmd === 'select_connection') config.provider = args.id;
      if (cmd === 'delete_connection') {
        if (config.provider === args.id && runtime.proxy === 'green') throw new Error('请先停止服务。');
        config.connections = config.connections.filter((p) => p.id !== args.id);
        if (config.provider === args.id) config.provider = '';
      }
      if (cmd === 'clear_connection_key') { const p = config.connections.find((p) => p.id === args.id); p.has_key = false; p.key_masked = ''; }
      if (cmd === 'set_mode') { config.mode = args.mode; runtime = { proxy: 'amber', sandbox: 'amber' }; }
      if (cmd === 'verify_connection' || cmd === 'test_connection') {
        if (window.testDelay) await new Promise((resolve) => setTimeout(resolve, window.testDelay));
        if (window.testFail) return { ok: false, hint: '上游返回 HTTP 401', status: 401 };
        return { ok: true, hint: '模型调用成功', latency_ms: 23 };
      }
      if (cmd === 'list_models') {
        if (window.catalogDelay) await new Promise((resolve) => setTimeout(resolve, window.catalogDelay));
        if (args.query.key === 'invalid-test-key') return { ok: false, hint: 'API Key 无效或未授权。' };
        return { ok: true, models: [{ id: 'gpt-6.1-sol', name: 'GPT 6.1' }, { id: 'claude-sonnet', name: 'Sonnet' }, { id: 'vendor/very-long-model-id-with-many-segments-and-a-large-context-window', name: '<unsafe-model>' }] };
      }
      if (cmd === 'one_click_login') { runtime = { proxy: 'green', sandbox: 'green' }; return { msg: '已启动 Science。' }; }
      if (cmd === 'stop_all') runtime = { proxy: 'amber', sandbox: 'amber' };
      if (cmd === 'read_usage') return { entries: [{ time: 1791547200, provider: 'deepseek', model: 'deepseek-v4-pro', input_tokens: 121, output_tokens: 32, latency_ms: 1234, ok: true }] };
      if (cmd === 'read_logs') return { proxy: '[20:00:00] Local gateway ready\n<unsafe-tag>', sandbox: 'Science ready' };
      if (cmd === 'diagnostics') return { science_installed: true, python: '/usr/bin/python3', config_dir: '~/.csswitch-next', science_data_dir: '~/.claude-science', science_auth_dir: '~/.csswitch-next/shared-science/auth', legacy_sandbox_home: '~/.csswitch-next/sandbox/home', version: '0.4.0', platform: 'aarch64', science_history: { projects: 21, conversations: 78, messages: 32754 } };
      if (cmd === 'set_config') Object.assign(config, args.cfg);
    } } };
  });
}

async function edit(page, name = 'DeepSeek') {
  await page.getByRole('button', { name: `编辑 ${name}`, exact: true }).click();
}
async function tab(page, name) { await page.getByRole('tab', { name, exact: true }).click(); }
async function chooseModel(page, role, query, id) {
  await page.getByRole('button', { name: `选择${role}`, exact: true }).click();
  await page.locator('#modelSearch').fill(query);
  await page.locator(`[data-model-value="${id}"]`).click();
}

test('add from preset, pick models, test an unsaved draft, save and activate', async ({ page }) => {
  const errors = []; page.on('pageerror', (e) => errors.push(e.message));
  await fixture(page); await page.goto('/');
  await page.getByRole('button', { name: '添加提供商', exact: true }).click();
  await page.locator('#presetSearch').fill('openai');
  await page.locator('[data-preset="openai"]').click();
  await expect(page.locator('#base_url')).toHaveValue('https://api.openai.com/v1');
  await page.locator('#name').fill('我的 Responses API');
  await page.locator('#key').fill('test-only-key-1234');
  await tab(page, '模型');
  await page.getByRole('button', { name: '获取模型列表', exact: true }).click();
  await expect(page.locator('#catalogStatus')).toContainText('3 个模型');
  await chooseModel(page, '默认模型', 'GPT', 'gpt-6.1-sol');
  await chooseModel(page, '质量模型', 'Sonnet', 'claude-sonnet');
  await page.locator('#fast_model').fill('custom-fast');
  await tab(page, '测试');
  await page.getByRole('button', { name: '测试连接', exact: true }).click();
  await expect(page.locator('#verification')).toContainText('测试通过');
  const before = await page.evaluate(() => window.testCalls);
  expect(before.some((c) => c.cmd === 'save_connection')).toBe(false);
  expect(before.find((c) => c.cmd === 'test_connection').args.input.model).toBe('gpt-6.1-sol');
  await page.getByRole('button', { name: '保存并使用', exact: true }).click();
  await expect(page.locator('#providerDialog')).not.toBeVisible();
  await expect(page.locator('.connection-row.selected')).toContainText('我的 Responses API');
  await expect(page.locator('.connection-row.selected')).toContainText('测试通过');
  await page.getByRole('button', { name: '一键开始', exact: true }).click();
  await expect(page.locator('#confirmBody')).toContainText('原有对话记录');
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#runtimeSection')).toContainText('运行中');
  await expect(page.locator('body')).not.toContainText('test-only-key-1234');
  expect(errors).toEqual([]);
});

test('modal tabs retain drafts, Escape guards edits and saved test state is invalidated', async ({ page }) => {
  await fixture(page); await page.goto('/'); await edit(page);
  await page.locator('#name').fill('未保存修改');
  await page.locator('#key').fill('temporary-test-key');
  await tab(page, '模型'); await page.locator('#model').fill('draft-model');
  await tab(page, '连接'); await expect(page.locator('#name')).toHaveValue('未保存修改');
  await page.keyboard.press('Escape');
  await expect(page.locator('#confirmDialog')).toBeVisible();
  await page.getByRole('button', { name: '取消', exact: true }).last().click();
  await expect(page.locator('#name')).toHaveValue('未保存修改');
  await tab(page, '测试'); await page.getByRole('button', { name: '测试连接', exact: true }).click();
  await expect(page.locator('#verification')).toContainText('测试通过');
  await tab(page, '模型'); await page.locator('#model').fill('changed-model');
  await tab(page, '测试'); await expect(page.locator('#verification')).toContainText('尚未测试');
  await page.getByRole('button', { name: '关闭配置', exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await edit(page); await expect(page.locator('#name')).toHaveValue('DeepSeek');
  await expect(page.locator('#key')).toHaveValue('');
});

test('overview quick switching, isolated tests, filtering and search', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.getByRole('button', { name: '切换到 GPT', exact: true }).click();
  await expect(page.locator('.connection-row.selected')).toContainText('GPT');
  await page.getByRole('button', { name: '测试 OpenRouter', exact: true }).click();
  await expect(page.locator('#confirmBody')).toContainText('不会切换当前提供商');
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('[data-edit="openrouter"]').first().locator('..')).toContainText('测试通过');
  await expect(page.locator('.connection-row.selected')).toContainText('GPT');
  await page.evaluate(() => { window.testFail = true; });
  await page.getByRole('button', { name: '测试 GPT', exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await page.locator('#providerFilter').selectOption('failed');
  await expect(page.locator('.connection-row')).toHaveCount(1);
  await expect(page.locator('.provider-status')).toContainText('测试失败');
  await page.locator('#providerFilter').selectOption('ready'); await expect(page.locator('.connection-row')).toHaveCount(2);
  await page.locator('#search').fill('nemotron'); await expect(page.locator('.connection-row')).toHaveCount(1);
  await page.locator('#search').fill('nothing'); await expect(page.locator('#connectionItems')).toContainText('没有匹配');
  await page.getByRole('button', { name: '清除筛选', exact: true }).click(); await expect(page.locator('.connection-row')).toHaveCount(4);
});

test('copy config excludes credentials; delete protection and key clearing', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.locator('.row-menu').filter({ has: page.locator('[aria-label="更多操作 GPT"]') }).locator('summary').click();
  await page.getByRole('button', { name: '复制配置', exact: true }).filter({ visible: true }).click();
  await expect(page.locator('#name')).toHaveValue('GPT 副本'); await expect(page.locator('#key')).toHaveValue('');
  await expect(page.locator('#key')).toHaveAttribute('placeholder', '填写 API Key');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await edit(page, 'GPT'); await page.getByRole('button', { name: '清除密钥', exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.getByRole('button', { name: '清除密钥', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '关闭配置', exact: true }).click();
  await page.locator('[aria-label="更多操作 GPT"]').click();
  await page.getByRole('button', { name: '删除连接', exact: true }).filter({ visible: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click(); await expect(page.locator('.connection-row')).toHaveCount(3);
  await page.locator('[aria-label="更多操作 DeepSeek"]').click();
  await expect(page.getByRole('button', { name: '删除连接', exact: true }).filter({ visible: true })).toBeEnabled();
  await page.getByRole('button', { name: '删除连接', exact: true }).filter({ visible: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('.connection-row')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '一键开始', exact: true })).toBeDisabled();
});

test('catalog failures, stale queries, endpoint changes and custom models keep the draft', async ({ page }) => {
  await fixture(page); await page.goto('/'); await edit(page);
  await page.locator('#name').fill('保留的修改'); await page.locator('#key').fill('invalid-test-key');
  await tab(page, '模型'); await page.getByRole('button', { name: '获取模型列表', exact: true }).click();
  await expect(page.locator('#catalogStatus')).toContainText('未授权'); await expect(page.locator('#model')).toHaveValue('deepseek-v4-pro');
  await tab(page, '连接'); await page.locator('#key').fill('valid-test-key');
  await page.evaluate(() => { window.catalogDelay = 450; });
  await tab(page, '模型'); await page.getByRole('button', { name: '获取模型列表', exact: true }).click();
  await tab(page, '连接'); await page.locator('#base_url').fill('https://other.example.com/v1');
  await tab(page, '模型'); await page.waitForTimeout(550);
  await expect(page.locator('#catalogStatus')).not.toContainText('已获取');
  await expect(page.locator('#model')).toHaveValue('deepseek-v4-pro');
  await page.getByRole('button', { name: '获取模型列表', exact: true }).click(); await expect(page.locator('#catalogStatus')).toContainText('已获取');
  await page.getByRole('button', { name: '选择默认模型', exact: true }).click();
  await page.locator('#modelSearch').fill('GPT'); await page.locator('#modelSearch').press('Enter');
  expect(await page.evaluate(() => window.testCalls.filter((c) => c.cmd === 'save_connection').length)).toBe(0);
  await page.locator('#modelSearch').press('ArrowDown'); await page.keyboard.press('Enter');
  await expect(page.locator('#model')).toHaveValue('gpt-6.1-sol');
  await chooseModel(page, '质量模型', 'Sonnet', 'claude-sonnet');
  await chooseModel(page, '质量模型', '', ''); await expect(page.locator('#quality_model')).toHaveValue('');
  await page.locator('#fast_model').fill('my-custom-fast-model');
  await page.getByRole('button', { name: '保存连接', exact: true }).click();
  const saved = await page.evaluate(() => window.testCalls.findLast((c) => c.cmd === 'save_connection').args.input);
  expect(saved.name).toBe('保留的修改'); expect(saved.fast_model).toBe('my-custom-fast-model');
  expect(await page.locator('unsafe-model').count()).toBe(0);
});

test('saved credentials auto-fetch once and model picker handles empty search safely', async ({ page }) => {
  await fixture(page); await page.goto('/'); await edit(page, 'GPT');
  await tab(page, '模型'); await expect(page.locator('#catalogStatus')).toContainText('3 个模型');
  await page.getByRole('button', { name: '选择默认模型', exact: true }).click();
  await page.locator('#modelSearch').fill('no-match'); await expect(page.locator('#modelOptions')).toContainText('没有匹配');
  await page.keyboard.press('Escape'); await expect(page.locator('#modelDialog')).not.toBeVisible();
  await expect(page.locator('#providerDialog')).toBeVisible();
  await page.getByRole('button', { name: '关闭配置', exact: true }).click(); await edit(page, 'GPT');
  expect(await page.evaluate(() => window.testCalls.filter((c) => c.cmd === 'list_models').length)).toBe(1);
});

test('validation moves to the invalid tab and tests neither save nor activate', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.getByRole('button', { name: '添加提供商', exact: true }).click(); await page.locator('[data-preset="openai"]').click();
  await page.locator('#key').fill('test-key');
  await page.getByRole('button', { name: '保存连接', exact: true }).click();
  await expect(page.getByRole('tab', { name: '模型', exact: true })).toHaveAttribute('aria-selected', 'true');
  await page.locator('#model').fill('unsaved-model'); await tab(page, '测试');
  await page.evaluate(() => { window.testFail = true; window.testDelay = 350; });
  await page.getByRole('button', { name: '测试连接', exact: true }).click();
  await expect(page.locator('#verification')).toContainText('正在测试');
  await expect(page.locator('#verification')).toContainText('测试失败');
  await expect(page.locator('#verification')).toContainText('401');
  const calls = await page.evaluate(() => window.testCalls);
  expect(calls.some((c) => ['save_connection', 'select_connection', 'one_click_login'].includes(c.cmd))).toBe(false);
});

test('logs, usage export, shared history settings, ports and official mode remain functional', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.getByRole('button', { name: '用量统计', exact: true }).click(); await expect(page.locator('table').first()).toContainText('121');
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '导出用量 CSV', exact: true }).click();
  expect((await download).suggestedFilename()).toBe('csswitch-usage.csv');
  await page.getByRole('button', { name: '运行日志', exact: true }).click(); await expect(page.locator('#logText')).toContainText('<unsafe-tag>');
  expect(await page.locator('unsafe-tag').count()).toBe(0);
  await page.getByRole('button', { name: '设置', exact: true }).click(); await expect(page.locator('#content')).toContainText('32,754');
  await page.locator('.settings-advanced > summary').click();
  await page.locator('#proxyPort').fill('8765'); await page.getByRole('button', { name: '保存端口', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('保留端口');
  await page.locator('#proxyPort').fill('18992'); await page.locator('#sandboxPort').fill('8765');
  await page.getByRole('button', { name: '保存端口', exact: true }).click(); await expect(page.locator('#notice')).toContainText('端口已保存');
  await page.getByRole('button', { name: '提供商', exact: true }).click();
  await page.getByRole('button', { name: '切换到 GPT', exact: true }).click();
  await page.getByRole('button', { name: '一键开始', exact: true }).click(); await page.getByRole('button', { name: '确认', exact: true }).click();
  await page.getByRole('button', { name: '官方 Claude', exact: true }).click(); await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.getByRole('button', { name: '打开官方 Science', exact: true })).toBeVisible();
  await expect(page.locator('#runtimeSection')).toHaveCount(0); await expect(page.locator('.connection-row')).toHaveCount(4);
});

test('overview, forms and picker fit desktop and mobile, with dark mode and visible assets', async ({ page }) => {
  const errors = []; page.on('pageerror', (e) => errors.push(e.message)); await fixture(page);
  mkdirSync('../artifacts/screenshots', { recursive: true });
  for (const [width, height] of [[1200, 800], [980, 720], [720, 780], [375, 812]]) {
    await page.setViewportSize({ width, height }); await page.goto('/');
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)).toBe(false);
    expect(await page.locator('.brand img').evaluate((img) => img.naturalWidth)).toBeGreaterThan(0);
    expect(await page.locator('svg.lucide').count()).toBeGreaterThan(15);
    await page.screenshot({ path: `../artifacts/screenshots/providers-${width}.png`, fullPage: true });
    await edit(page, 'GPT'); await tab(page, '模型'); await expect(page.locator('#catalogStatus')).toContainText('已获取');
    await page.screenshot({ path: `../artifacts/screenshots/provider-editor-${width}.png`, animations: 'disabled' });
    await page.getByRole('button', { name: '选择默认模型', exact: true }).click();
    await page.locator('#modelSearch').fill('vendor');
    const boxes = await page.locator('#modelDialog, #modelOptions button').evaluateAll((es) => es.map((e) => ({ left: e.getBoundingClientRect().left, right: e.getBoundingClientRect().right, top: e.getBoundingClientRect().top, bottom: e.getBoundingClientRect().bottom })));
    expect(boxes.every((b) => b.left >= 0 && b.right <= width && b.top >= 0 && b.bottom <= height)).toBe(true);
    await page.screenshot({ path: `../artifacts/screenshots/model-picker-${width}.png`, animations: 'disabled' });
  }
  await page.setViewportSize({ width: 980, height: 720 }); await page.goto('/');
  await page.getByRole('button', { name: '切换深浅主题', exact: true }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.screenshot({ path: '../artifacts/screenshots/providers-dark.png', fullPage: true });
  expect(errors).toEqual([]);
});

test('browser preview never starts a service or claims an API is working', async ({ page }) => {
  await page.goto('/'); await expect(page.locator('#previewLabel')).toBeVisible();
  await expect(page.getByRole('button', { name: '一键开始', exact: true })).toBeDisabled();
  await expect(page.locator('.connection-row')).toHaveCount(0);
  await expect(page.locator('#connectionItems')).toContainText('暂无提供商');
  await expect(page.locator('#runtimeSection')).toContainText('未启动');
});

test('every saved connection can be removed, including old presets and the last active one', async ({ page }) => {
  await fixture(page); await page.goto('/');
  for (const name of ['DeepSeek', '通义千问', 'GPT', 'OpenRouter']) {
    await page.locator(`[aria-label="更多操作 ${name}"]`).click();
    const remove = page.getByRole('button', { name: '删除连接', exact: true }).filter({ visible: true });
    await expect(remove).toBeEnabled(); await remove.click();
    await page.getByRole('button', { name: '确认', exact: true }).click();
  }
  await expect(page.locator('.connection-row')).toHaveCount(0);
  await expect(page.locator('#connectionItems')).toContainText('暂无提供商');
  await page.getByRole('button', { name: '刷新状态', exact: true }).click();
  await expect(page.locator('.connection-row')).toHaveCount(0);
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.locator('.settings-advanced > summary').click();
  await page.locator('#proxyPort').fill('18993');
  await page.getByRole('button', { name: '保存端口', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('端口已保存');
});

test('active connection deletion is protected only while the gateway runs', async ({ page }) => {
  await fixture(page); await page.goto('/');
  await page.getByRole('button', { name: '切换到 GPT', exact: true }).click();
  await page.getByRole('button', { name: '一键开始', exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#runtimeSection')).toContainText('运行中');
  await page.locator('[aria-label="更多操作 GPT"]').click();
  await expect(page.getByRole('button', { name: '删除连接', exact: true }).filter({ visible: true })).toBeDisabled();
  await page.getByRole('button', { name: '停止网关与 Science', exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#runtimeSection')).toContainText('未启动');
  await page.locator('[aria-label="更多操作 GPT"]').click();
  await expect(page.getByRole('button', { name: '删除连接', exact: true }).filter({ visible: true })).toBeEnabled();
});
