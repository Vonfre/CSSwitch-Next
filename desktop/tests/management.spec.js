import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function setup(page, offline = false) {
  await page.addInitScript(({ offline }) => {
    const now = Math.floor(Date.now() / 1000);
    let skills = [{ name: 'paper-reader', description: '阅读科学文献并提取方法', source: 'local', enabled: true }, { name: 'data-analysis', description: '处理实验数据', source: 'anthropic', enabled: true }, { name: 'blocked', description: '<unsafe-skill>', source: 'organization', admin_blocked: true, enabled: false }];
    let connectors = [{ id: 'local:memory', name: 'memory', description: '知识记忆', source: 'local-stdio', transport: 'stdio', attachedAgents: ['OPERON'] }, { id: 'remote', name: 'remote', description: '远程数据库', source: 'custom', transport: 'streamable_http', attachedAgents: [] }];
    const sessions = Array.from({ length: 28 }, (_, i) => ({ key: 'org:session-' + i, id: 'session-' + i, org: 'org', title: i === 0 ? '蛋白质结构研究' : i === 1 ? '<unsafe-session>' : '文献分析 ' + i, project_id: i % 2 ? 'papers' : 'proteins', project_name: i % 2 ? '文献综述' : '蛋白研究', messages: i === 0 ? 101 : 1, model: 'gpt-research', status: 'completed', updated_at: now * 1000 - i * 86400000 }));
    const repos = []; const pricing = { 'gpt-research': { input: 2, output: 8, cache_read: 0.2, cache_write: 2.5 } };
    window.testCalls = []; window.__TAURI__ = { core: { invoke: async (cmd, args = {}) => {
      window.testCalls.push({ cmd, args });
      if (cmd === 'get_config') return { provider: 'test', mode: 'proxy', proxy_port: 18992, sandbox_port: 8991, connections: [{ id: 'test', name: '研究 API', protocol: 'responses', model: 'gpt-research', base_url: 'https://example.com', has_key: true }] };
      if (cmd === 'status') return { proxy: 'amber', sandbox: 'amber' };
      if (cmd === 'read_usage') return { entries: Array.from({ length: 31 }, (_, i) => ({ time: now - i * 3600, provider: i === 30 ? 'other' : 'test', model: i === 30 ? 'unpriced' : 'gpt-research', protocol: 'responses', input_tokens: 1000, output_tokens: 250, cache_read_input_tokens: 400, latency_ms: 850, ok: i !== 1 })) };
      if (cmd === 'mcp_manage') {
        const { operation: op, params: p } = args;
        if (offline && op.startsWith('skills_')) throw new Error('请先启动第三方 Science。');
        if (op === 'skills_list') return { skills: structuredClone(skills), degraded: Boolean(window.catalogDegraded), localImportAvailable: Boolean(window.localImportAvailable), customAllowed: !window.customSkillsBlocked };
        if (op === 'skills_content') return { content: '---\nname: ' + p.name + '\n---\n<unsafe-content>\n# Methods' };
        if (op === 'skills_enabled') skills.find(s => s.name === p.name).enabled = p.enabled;
        if (op === 'skills_remove') skills = skills.filter(s => s.name !== p.name);
        if (op === 'skills_import') { if (window.importFails) throw new Error('Science HTTP 409：同名技能已存在。'); skills.push({ name: p.filename.replace(/\.zip|\.md/g, ''), source: window.catalogDegraded ? 'csswitch-local' : 'local', description: '新技能' }); return { message: 'Skill 已导入 Science。' }; }
        if (op === 'list') return { connectors: structuredClone(connectors), customAllowed: true };
        if (op === 'add_local' || op === 'add_remote') connectors.push({ id: 'local:' + p.name, name: p.name, source: op === 'add_local' ? 'local-stdio' : 'custom', transport: op === 'add_local' ? 'stdio' : p.transport, attachedAgents: p.attach ? ['OPERON'] : [] });
        return { message: '已更新。' };
      }
      if (cmd === 'workspace_manage') {
        const { operation: op, params: p } = args;
        if (op === 'skills_discover') return { skills: [{ key: 'codex:paper-reader', name: 'paper-reader', directory: 'paper-reader', source: 'codex', description: '已安装技能', path: '~/.codex/skills/paper-reader' }, { key: 'agents:lab-notes', name: 'lab-notes', directory: 'lab-notes', source: 'agents', description: '整理实验笔记', path: '~/.agents/skills/lab-notes' }], repos: structuredClone(repos) };
        if (op === 'skill_bundle') return { filename: 'lab-notes.zip', bundle: 'dGVzdA==' };
        if (op === 'repo_add') { if (window.repoFails) throw new Error('无法下载 GitHub 仓库。'); repos.push({ key: p.repo + '@' + p.branch, repo: p.repo, branch: p.branch }); }
        if (op === 'repo_remove') repos.splice(repos.findIndex(r => r.key === p.key), 1);
        if (op === 'sessions_list') return { sessions: structuredClone(sessions) };
        if (op === 'session_meta') Object.assign(sessions.find(s => s.key === p.key), p.changes);
        if (op === 'session_messages') {
          const many = p.key === 'org:session-0', total = many ? 101 : 1;
          return { total, offset: p.offset, has_more: many && p.offset === 0, messages: Array.from({ length: many && p.offset === 0 ? 100 : 1 }, (_, i) => ({ idx: p.offset + i, role: i % 2 ? 'assistant' : 'user', text: '<unsafe-message>\n研究方法 ' + (p.offset + i + 1) })) };
        }
        if (op === 'pricing_get') return { pricing: structuredClone(pricing) };
        if (op === 'pricing_save') pricing[p.model] = p.rates;
        return { message: '已保存。' };
      }
    } } };
  }, { offline });
  await page.goto('/');
}

test('Skills supports preview, source filtering, enable, uninstall and local installation', async ({ page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await setup(page); await page.getByRole('button', { name: 'Skills 管理', exact: true }).click();
  await expect(page.locator('.management-row')).toHaveCount(3);
  await expect(page.getByRole('switch', { name: '启用 blocked', exact: true })).toBeDisabled();
  await page.locator('.management-row').first().getByRole('button', { name: '查看内容' }).click();
  await expect(page.locator('#workspaceText')).toContainText('<unsafe-content>'); await expect(page.locator('unsafe-content')).toHaveCount(0);
  await page.getByRole('button', { name: '关闭内容' }).click();
  await page.getByRole('switch', { name: '启用 paper-reader', exact: true }).click();
  await expect(page.getByRole('switch', { name: '启用 paper-reader', exact: true })).toHaveAttribute('aria-checked', 'false');
  await page.getByRole('button', { name: '发现', exact: false }).click();
  await page.getByLabel('搜索 Skills').fill('lab'); await expect(page.locator('.management-row')).toHaveCount(1);
  await page.getByRole('button', { name: '安装到 Science', exact: true }).click();
  await expect(page.locator('.management-row button')).toContainText('已安装');
  await page.getByLabel('搜索 Skills').fill(''); await page.getByRole('button', { name: '已安装', exact: false }).first().click();
  await page.getByRole('button', { name: '卸载 lab-notes', exact: true }).click(); await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#skillRows')).not.toContainText('lab-notes'); expect(errors).toEqual([]);
});

test('repository errors retain inputs and offline Science still allows local discovery', async ({ page }) => {
  await setup(page, true); await page.getByRole('button', { name: 'Skills 管理', exact: true }).click();
  await expect(page.locator('#content')).toContainText('请先启动'); await page.getByRole('button', { name: '发现', exact: false }).click();
  await expect(page.locator('.management-row')).toHaveCount(2); await expect(page.getByRole('button', { name: '安装到 Science' }).first()).toBeDisabled();
  await page.getByRole('button', { name: '仓库管理', exact: true }).click();
  await page.getByLabel('GitHub 仓库', { exact: true }).fill('anthropics/skills'); await page.evaluate(() => { window.repoFails = true; });
  await page.getByRole('button', { name: '添加并同步' }).click(); await expect(page.locator('#repoError')).toContainText('无法下载');
  await expect(page.getByLabel('GitHub 仓库', { exact: true })).toHaveValue('anthropics/skills');
  await page.evaluate(() => { window.repoFails = false; }); await page.getByRole('button', { name: '添加并同步' }).click();
  await expect(page.locator('#workspaceDialog')).not.toBeVisible(); await page.getByRole('button', { name: '仓库管理', exact: true }).click();
  await expect(page.locator('.repo-row')).toContainText('anthropics/skills'); await page.getByRole('button', { name: '移除 anthropics/skills' }).click();
  await expect(page.locator('.repo-row')).toHaveCount(0);
});

test('file Skill import uses native API and failed imports are visible', async ({ page }) => {
  await setup(page); await page.getByRole('button', { name: 'Skills 管理', exact: true }).click();
  await page.evaluate(() => { window.importFails = true; });
  await page.locator('#skillFile').setInputFiles({ name: 'new-skill.md', mimeType: 'text/markdown', buffer: Buffer.from('---\nname: new-skill\ndescription: Fixture\n---\n') });
  await expect(page.locator('#notice')).toContainText('409');
  await page.evaluate(() => { window.importFails = false; }); await page.locator('#skillFile').setInputFiles({ name: 'new-skill.md', mimeType: 'text/markdown', buffer: Buffer.from('---\nname: new-skill\n---\n') });
  await expect(page.locator('#skillRows')).toContainText('new-skill');
  const call = await page.evaluate(() => window.testCalls.find(c => c.args.operation === 'skills_import')); expect(call.args.params.bundle).toBeTruthy();
});

test('degraded Science catalog disables import and uninstall while preserving discovery and toggles', async ({ page }) => {
  await setup(page); await page.evaluate(() => { window.catalogDegraded = true; });
  await page.getByRole('button', { name: 'Skills 管理', exact: true }).click();
  await expect(page.locator('#content')).toContainText('当前无法导入');
  await expect(page.getByRole('button', { name: '导入 Skill', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '卸载 paper-reader', exact: true })).toBeDisabled();
  await expect(page.getByRole('switch', { name: '启用 paper-reader', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '发现', exact: false }).click();
  await expect(page.locator('.management-row')).toHaveCount(2);
  await expect(page.getByRole('button', { name: '安装到 Science', exact: true })).toBeDisabled();
});

test('degraded cloud catalog allows verified local installation and uninstall', async ({ page }) => {
  await setup(page); await page.evaluate(() => { window.catalogDegraded = true; window.localImportAvailable = true; });
  await page.getByRole('button', { name: 'Skills 管理', exact: true }).click();
  await expect(page.locator('#content')).toContainText('本地 Skills 仍可安装');
  await expect(page.getByRole('button', { name: '导入 Skill', exact: true })).toBeEnabled();
  await expect(page.getByRole('button', { name: '卸载 paper-reader', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: '发现', exact: false }).click();
  await page.getByRole('button', { name: '安装到 Science', exact: true }).click();
  await page.getByRole('button', { name: '已安装', exact: false }).first().click();
  await expect(page.getByRole('button', { name: '卸载 lab-notes', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '卸载 lab-notes', exact: true }).click();
  await page.getByRole('button', { name: '确认', exact: true }).click();
  await expect(page.locator('#skillRows')).not.toContainText('lab-notes');
});

test('local import remains disabled when Science policy blocks custom skills', async ({ page }) => {
  await setup(page); await page.evaluate(() => { window.catalogDegraded = true; window.localImportAvailable = true; window.customSkillsBlocked = true; });
  await page.getByRole('button', { name: 'Skills 管理', exact: true }).click();
  await expect(page.getByRole('button', { name: '导入 Skill', exact: true })).toBeDisabled();
  await expect(page.locator('#content')).toContainText('组织策略禁止');
  await page.getByRole('button', { name: '发现', exact: false }).click();
  await expect(page.getByRole('button', { name: '安装到 Science', exact: true })).toBeDisabled();
});

test('sessions filter, paginate, render escaped transcripts, export all pages, rename and archive', async ({ page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message));
  await setup(page); await page.getByRole('button', { name: '会话管理', exact: true }).click();
  await expect(page.locator('.session-row')).toHaveCount(25); await page.getByRole('button', { name: '下一页', exact: true }).click(); await expect(page.locator('.session-row')).toHaveCount(3);
  await page.getByRole('button', { name: '上一页', exact: true }).click();
  await page.getByRole('button', { name: '查看 蛋白质结构研究', exact: true }).click(); await expect(page.locator('.message')).toHaveCount(100);
  await expect(page.locator('unsafe-message')).toHaveCount(0); await page.getByRole('button', { name: '加载更多消息' }).click(); await expect(page.locator('.message')).toHaveCount(101);
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '导出会话 Markdown' }).click(); expect((await download).suggestedFilename()).toBe('csswitch-session-session-0.md');
  await page.getByRole('button', { name: '关闭会话阅读' }).click(); await page.getByRole('button', { name: '收藏 蛋白质结构研究', exact: true }).click();
  await page.getByRole('button', { name: '收藏', exact: true }).click(); await expect(page.locator('.session-row')).toHaveCount(1);
  await page.getByRole('button', { name: '重命名 蛋白质结构研究', exact: true }).click(); await page.getByLabel('名称', { exact: true }).fill('新研究名称'); await page.getByRole('button', { name: '保存名称' }).click();
  await expect(page.locator('.session-row')).toContainText('新研究名称'); await page.getByRole('button', { name: '归档 新研究名称', exact: true }).click(); await expect(page.locator('.session-row')).toHaveCount(0);
  await page.getByRole('button', { name: '已归档', exact: true }).click(); await page.getByRole('button', { name: '恢复 新研究名称', exact: true }).click();
  await page.getByRole('button', { name: '全部会话', exact: true }).click(); await page.getByLabel('搜索会话').fill('新研究'); await expect(page.locator('.session-row')).toHaveCount(1); expect(errors).toEqual([]);
});

test('usage filters, pagination, cache-aware costs, pricing persistence and filtered export', async ({ page }) => {
  await setup(page); await page.getByRole('button', { name: '用量统计', exact: true }).click();
  await expect(page.locator('.metrics')).toContainText('1 条请求未设置定价'); await expect(page.locator('#usageChart .chart-column')).toHaveCount(14);
  // Responses input includes cached tokens: (600*2 + 250*8 + 400*.2)/1e6 = .00328.
  await expect(page.locator('#requestTable')).toContainText('$0.0033');
  await page.getByRole('button', { name: '请求记录', exact: true }).click(); await expect(page.locator('#requestTable tbody tr')).toHaveCount(25);
  await page.getByRole('button', { name: '下一页', exact: true }).click(); await expect(page.locator('#requestTable tbody tr')).toHaveCount(6);
  await page.getByLabel('用量提供商').selectOption('other'); await expect(page.locator('#requestTable tbody tr')).toHaveCount(1);
  const download = page.waitForEvent('download'); await page.getByRole('button', { name: '导出用量 CSV' }).click(); expect((await download).suggestedFilename()).toBe('csswitch-usage.csv');
  await page.getByRole('button', { name: '模型定价', exact: true }).click(); await page.getByRole('button', { name: '添加模型定价' }).click();
  await page.getByLabel('模型 ID').fill('unpriced'); await page.getByLabel('输入（USD / 百万 Tokens）', { exact: true }).fill('1'); await page.getByLabel('输出（USD / 百万 Tokens）', { exact: true }).fill('4'); await page.getByRole('button', { name: '保存定价' }).click();
  await page.getByRole('button', { name: '总览', exact: true }).click(); await expect(page.locator('.metrics')).toContainText('$0.0016');
  await page.getByRole('button', { name: '刷新状态', exact: true }).click(); await expect(page.locator('.metrics')).toContainText('$0.0016');
});

test('MCP search and cc-switch JSON import validate before native mutations, skip duplicates and clear secrets', async ({ page }) => {
  await setup(page); await page.getByRole('button', { name: 'MCP 管理', exact: true }).click();
  await page.getByLabel('搜索 MCP').fill('memory'); await expect(page.locator('.mcp-row:visible')).toHaveCount(1); await page.getByLabel('搜索 MCP').fill('');
  await page.getByLabel('MCP 绑定状态').selectOption('unbound'); await expect(page.locator('.mcp-row:visible')).toHaveCount(1); await page.getByLabel('MCP 绑定状态').selectOption('all');
  await page.getByRole('button', { name: '导入 JSON' }).click(); await page.getByLabel('MCP 配置 JSON').fill('{"mcpServers":{"valid":{"command":"npx"},"invalid":{"url":"http://bad.example"}}}');
  await page.getByRole('button', { name: '导入配置', exact: true }).click(); await expect(page.locator('#mcpImportError')).toContainText('HTTPS');
  expect(await page.evaluate(() => window.testCalls.filter(c => c.args.operation === 'add_local').length)).toBe(0);
  await page.getByLabel('MCP 配置 JSON').fill('{"mcpServers":{"memory":{"command":"npx"},"new-server":{"command":"npx","args":[],"env":{"KEY":"private-draft"}}}}');
  await page.getByRole('button', { name: '导入配置', exact: true }).click(); await expect(page.locator('#mcpImportReport')).toContainText('已跳过'); await expect(page.locator('#mcpImportReport')).toContainText('new-server：已导入');
  await expect(page.getByLabel('MCP 配置 JSON')).toHaveValue(''); await page.getByRole('button', { name: '关闭', exact: true }).click(); await expect(page.locator('#mcpDialog')).toBeEmpty(); await expect(page.locator('.mcp-list')).toContainText('new-server');
});

test('management pages fit small windows, support dark mode and have no script errors', async ({ page }) => {
  const errors = []; page.on('pageerror', e => errors.push(e.message)); await setup(page); mkdirSync('../artifacts/screenshots', { recursive: true });
  for (const [width, height] of [[1200, 820], [980, 720], [390, 844]]) {
    await page.setViewportSize({ width, height });
    for (const [label, id] of [['MCP 管理', 'mcp'], ['Skills 管理', 'skills'], ['用量统计', 'usage'], ['会话管理', 'sessions']]) {
      await page.getByRole('button', { name: label, exact: true }).click(); await expect(page.locator('#content')).toHaveAttribute('aria-busy', 'false');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `../artifacts/screenshots/${id}-workspace-${width}.png`, fullPage: true });
    }
  }
  await page.getByRole('button', { name: '查看 蛋白质结构研究', exact: true }).click(); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '切换深浅主题' }).click(); await page.screenshot({ path: '../artifacts/screenshots/session-reader-dark.png', fullPage: true });
  await expect(page.locator('unsafe-skill, unsafe-session, unsafe-message')).toHaveCount(0); expect(errors).toEqual([]);
});
