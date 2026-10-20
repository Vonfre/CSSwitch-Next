import { test, expect } from '@playwright/test';

async function setup(page, stopped = false, blocked = false) {
  await page.addInitScript(({ stopped, blocked }) => {
    let features = { science_local_mcp: true, science_zh: true };
    let connectors = [{ id: 'local:sample', name: 'sample', description: '<untrusted>', source: 'local-stdio', transport: 'stdio', authState: 'not-required', attachedAgents: [] },
      { id: 'remote-id', name: 'remote', source: 'custom', transport: 'streamable_http', authState: 'unauthorized', attachedAgents: [] }];
    window.testCalls = [];
    window.setMcpRows = rows => { connectors = rows; };
    window.__TAURI__ = { core: { invoke: async (cmd, args = {}) => {
      window.testCalls.push({ cmd, args });
      if (cmd === 'get_config') return { ...features, connections: [], mode: 'proxy', provider: '', proxy_port: 18992, sandbox_port: 8991 };
      if (cmd === 'set_science_features') {
        if (window.featureFailure) throw new Error('设置保存失败，请重试');
        features = { science_local_mcp: args.localMcp, science_zh: args.zh }; return { message: '已保存' };
      }
      if (cmd === 'status') return { proxy: 'amber', sandbox: 'amber' };
      if (cmd === 'mcp_manage') {
        if (stopped) throw new Error('请先启动 CSSwitch 所管理的第三方 Science。');
        const { operation: op, params: p } = args;
        if (op === 'list') return { scienceProfiles: connectors.some(c => c.name === 'csswitch-local-pubmed') ? Array.from({length:24}, () => ({ready:true})) : [], connectors, customAllowed: !blocked, directoryHealth: { ok: false, status: 401 } };
        if (op === 'tools') return { status: 'loaded', tools: [{ toolName: 'search', description: '<unsafe tool>', readOnlyHint: true, state: 'unset' }] };
        if (op === 'attach') connectors.find(c => c.id === p.id).attachedAgents = ['OPERON'];
        if (op === 'detach') connectors.find(c => c.id === p.id).attachedAgents = [];
        if (op === 'remove') connectors = connectors.filter(c => c.id !== p.id);
        if (op === 'add_local' || op === 'add_remote' || op === 'install') {
          if (window.mcpFailure) throw new Error('Science HTTP 400：参数被拒绝');
          connectors.push({ id: op === 'install' ? 'local:csswitch-local-pubmed' : 'local:new', name: op === 'install' ? 'csswitch-local-pubmed' : p.name, source: op === 'add_remote' ? 'custom' : 'local-stdio', transport: 'stdio', attachedAgents: ['OPERON'] });
          return op === 'install' ? {complete: true, message:'本机连接器接入完成：24/24 已连接。', report:[{displayName:'PubMed',ok:true,tools:7}]} : { message: 'MCP 已添加。' };
        }
        return { message: op === 'authorize' ? '请在浏览器完成授权后刷新。' : '已更新。' };
      }
    } } };
  }, { stopped, blocked });
  await page.goto('/'); await page.getByRole('button', { name: 'MCP 管理', exact: true }).click();
}

test('directory 401 does not block independent MCP; binding, tools, permissions, OAuth and deletion work', async ({ page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await setup(page);
  await page.getByRole('button', { name: '官方目录说明', exact: true }).click();
  await expect(page.locator('#helpPopover')).toContainText('HTTP 401');
  await page.keyboard.press('Escape');
  await expect(page.locator('untrusted')).toHaveCount(0);
  const row = page.locator('[data-mcp-id="local:sample"]');
  await row.getByRole('button', { name: '绑定 Science' }).click();
  await expect(row).toContainText('OPERON');
  await row.getByRole('button', { name: '工具', exact: true }).click();
  await expect(page.locator('#mcpTools')).toContainText('search');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByLabel('search 权限').selectOption('ask');
  await expect(page.locator('#notice')).toContainText('工具权限已更新');
  await page.locator('[data-mcp-id="remote-id"]').getByRole('button', { name: '授权', exact: true }).click();
  await expect(page.locator('#notice')).toContainText('浏览器');
  await row.getByRole('button', { name: '解绑 Science' }).click();
  await expect(row).toContainText('未绑定 Science');
  await row.getByRole('button', { name: '删除 sample' }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(row).toHaveCount(0);
  const calls = await page.evaluate(() => window.testCalls);
  expect(calls.some(c => c.args.operation === 'grant' && c.args.params.decision === 'ask')).toBe(true);
  expect(errors).toEqual([]);
});

test('local MCP preference works before Science starts, preserves translation and recovers from save failure', async ({ page }) => {
  await setup(page, true);
  const toggle = page.getByRole('switch', { name: '使用本机 MCP', exact: true });
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await page.evaluate(() => { window.featureFailure = true; }); await toggle.click();
  await expect(page.locator('#notice')).toContainText('设置保存失败');
  await expect(toggle).toHaveAttribute('aria-checked', 'true');
  await page.evaluate(() => { window.featureFailure = false; }); await toggle.click();
  await expect(toggle).toHaveAttribute('aria-checked', 'false');
  const calls = await page.evaluate(() => window.testCalls);
  expect(calls.filter(c => c.cmd === 'set_science_features').at(-1).args).toEqual({ localMcp: false, zh: true });
  expect(calls.some(c => ['one_click_login', 'stop_all'].includes(c.cmd))).toBe(false);
  expect(calls.filter(c => c.cmd === 'mcp_manage').every(c => c.args.operation === 'list')).toBe(true);
});

test('dense connector list keeps controls compact and contextual help inside a narrow viewport', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => {
    window.setMcpRows(['BioMart', 'Cancer Models', 'CellGuide', 'Chemistry', 'Clinical Genomics', 'Drug Regulatory', 'Expression', 'Genes & Ontologies', 'Genomes', 'Human Genetics', 'Ketcher Chemistry', 'Literature Graph'].map((name, i) => ({
      id: 'bundled:' + i, name, displayName: name, source: 'bundled', transport: 'stdio', enabled: true,
      description: 'Science 本机科研连接器，保留原始工具参数、审批与数据源许可。', connectionStatus: 'connected', attachedAgents: ['OPERON'],
    })));
  });
  await page.locator('#refreshBtn').click();
  await expect(page.locator('.mcp-row')).toHaveCount(12);
  expect(await page.locator('.mcp-row').first().evaluate(el => el.getBoundingClientRect().top)).toBeLessThan(370);
  await page.screenshot({ path: '../artifacts/mcp-redesign-dense.png' });
  await page.setViewportSize({ width: 390, height: 844 }); await page.locator('#themeBtn').click();
  await page.getByRole('button', { name: '本机 MCP说明', exact: true }).focus(); await page.keyboard.press('Enter');
  await expect(page.locator('#helpPopover')).toBeVisible();
  const bounds = await page.locator('#helpPopover').boundingBox();
  expect(bounds.x).toBeGreaterThanOrEqual(0); expect(bounds.x + bounds.width).toBeLessThanOrEqual(390);
  await page.keyboard.press('Escape');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('#addMcp')).toHaveCSS('background-color', 'rgb(237, 237, 237)');
  await expect(page.locator('#localMcpToggle')).toHaveCSS('background-color', 'rgb(237, 237, 237)');
  await page.screenshot({ path: '../artifacts/mcp-redesign-mobile.png', animations: 'disabled' });
});

test('local add validates JSON, retains errors, and clears secret drafts after save', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: '添加 MCP', exact: true }).click();
  await page.getByLabel('名称', { exact: true }).fill('new-mcp');
  await page.getByLabel('可执行文件绝对路径').fill('/usr/bin/python3');
  await page.getByLabel('参数（JSON 字符串数组）').fill('invalid');
  await page.locator('#mcpForm').getByRole('button', { name: '添加', exact: true }).click();
  await expect(page.locator('#mcpFormError')).toContainText('JSON');
  await page.getByLabel('参数（JSON 字符串数组）').fill('["/tmp/mcp.py"]');
  await page.getByLabel('环境变量（JSON 字符串对象，可选）').fill('{"SERVICE_KEY":"test-secret-123"}');
  await page.evaluate(() => { window.mcpFailure = true; });
  await page.locator('#mcpForm').getByRole('button', { name: '添加', exact: true }).click();
  await expect(page.locator('#mcpFormError')).toContainText('HTTP 400');
  await expect(page.locator('#mcpDialog')).toBeVisible();
  await page.evaluate(() => { window.mcpFailure = false; });
  await page.locator('#mcpForm').getByRole('button', { name: '添加', exact: true }).click();
  await expect(page.locator('#mcpDialog')).not.toBeVisible();
  await expect(page.locator('#mcpDialog')).toBeEmpty();
  await expect(page.locator('#content')).toContainText('new-mcp');
  await expect(page.locator('body')).not.toContainText('test-secret-123');
});

test('install and remote HTTPS setup use distinct commands', async ({ page }) => {
  await setup(page);
  await page.getByRole('button', { name: '接入 24 个本机连接器', exact: true }).click();
  await page.getByRole('button', { name: '接入并绑定', exact: true }).click();
  await expect(page.locator('#mcpInstallReport')).toContainText('PubMed：已接入（7 个工具）');
  await expect(page.locator('#content')).toContainText('24/24 已连接');
  await page.getByRole('button', { name: '取消', exact: true }).click();
  await page.getByRole('button', { name: '添加 MCP', exact: true }).click();
  await page.getByLabel('名称', { exact: true }).fill('remote2');
  await page.getByLabel('连接方式').selectOption('remote');
  await page.getByLabel('公共 HTTPS MCP 地址').fill('https://example.com/mcp');
  await page.locator('#mcpForm').getByRole('button', { name: '添加', exact: true }).click();
  await expect(page.locator('#mcpDialog')).not.toBeVisible();
  const calls = await page.evaluate(() => window.testCalls);
  expect(calls.some(c => c.args.operation === 'install')).toBe(true);
  expect(calls.find(c => c.args.operation === 'add_remote').args.params.url).toBe('https://example.com/mcp');
});

test('stopped Science and organization policy have clear unavailable states', async ({ page }) => {
  await setup(page, true);
  await expect(page.locator('#content')).toContainText('请先启动');
  await expect(page.getByRole('button', { name: '添加 MCP', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '返回提供商' }).click();
  await expect(page.locator('#pageTitle')).toHaveText('提供商');
});

test('organization policy prevents adding custom MCP', async ({ page }) => {
  await setup(page, false, true);
  await expect(page.getByRole('button', { name: '添加 MCP', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '接入 24 个本机连接器', exact: true })).toBeDisabled();
});

test('MCP page and installation form fit narrow windows and dark mode', async ({ page }) => {
  await setup(page);
  await page.screenshot({ path: '../artifacts/mcp-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: '切换深浅主题' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '接入 24 个本机连接器', exact: true }).click();
  await expect(page.locator('#mcpDialog')).toBeVisible();
  expect(await page.locator('#mcpDialog').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
  await page.screenshot({ path: '../artifacts/mcp-mobile.png', fullPage: true });
});
