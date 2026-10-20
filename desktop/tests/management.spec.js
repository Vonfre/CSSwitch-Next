import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function setup(page, offline = false) {
  await page.addInitScript(({ offline }) => {
    const now = Math.floor(Date.now() / 1000);
    let skills = [{ name: 'paper-reader', description: '阅读科学文献并提取方法', source: 'local', enabled: true }, { name: 'data-analysis', description: '处理实验数据', source: 'anthropic', enabled: true }, { name: 'blocked', description: '<unsafe-skill>', source: 'organization', admin_blocked: true, enabled: false }];
    window.addManagedFixture = (file = false) => skills.push({name:'lab-notes',description:'实验笔记',source:'csswitch-local',sourceKey:file ? null : 'agents:lab-notes',fingerprint:'old-content',enabled:false,attachedAgents:['OPERON']});
    let connectors = [{ id: 'local:memory', name: 'memory', description: '知识记忆', source: 'local-stdio', transport: 'stdio', attachedAgents: ['OPERON'] }, { id: 'remote', name: 'remote', description: '远程数据库', source: 'custom', transport: 'streamable_http', attachedAgents: [] }];
    window.addHostedFixture = () => connectors.push({id:'bundled:pubmed',name:'pubmed',source:'bundled',transport:'streamable_http',enabled:false,policyDisabled:true,attachedAgents:['OPERON']});
    const sessions = Array.from({ length: 28 }, (_, i) => ({ key: 'org:session-' + i, id: 'session-' + i, org: 'org', title: i === 0 ? '蛋白质结构研究' : i === 1 ? '<unsafe-session>' : '文献分析 ' + i, project_id: i % 2 ? 'papers' : 'proteins', project_name: i % 2 ? '文献综述' : '蛋白研究', messages: i === 0 ? 101 : 1, model: 'gpt-research', status: 'completed', preview:'首条提问 '+i,last_preview:'研究结果摘要 '+i,created_at:now*1000-i*86400000-600000,updated_at: now * 1000 - i * 86400000 }));
    window.useDesignPreview = () => {
      skills = [['paper-reader','阅读科学文献，提取研究问题、方法与核心结论。','csswitch-local'],['pubmed-search','检索 PubMed 文献，整理证据与引用。','csswitch-local'],['data-analysis','探索实验数据，进行统计检验并生成图表。','anthropic'],['protein-structure','分析蛋白质结构、置信度与关键结合位点。','csswitch-local'],['lab-notes','整理实验笔记，记录步骤、观察和后续计划。','csswitch-local'],['literature-review','系统梳理文献，构建研究问题与证据框架。','csswitch-local']].map(([name,description,source],i)=>({name,description,source,enabled:i!==4}));
      const titles=['蛋白质结构研究','单细胞数据分析与批次校正','整理肿瘤免疫相关文献','设计蛋白结合位点验证实验','RNA-seq 差异表达分析','研究计划与实验记录'];
      sessions.splice(5); sessions.forEach((r,i)=>{r.project_id='design-'+(i===0?0:i<4?1:2);r.project_name=i===0?'蛋白研究':i<4?'单细胞图谱':'文献综述';r.last_preview='已整理分析步骤、数据处理方法和后续验证方案。';r.title=titles[i%titles.length];r.preview=i===0?'比较 AlphaFold 预测结构和实验结构，整理验证方案。':'整理研究步骤、数据处理方法与验证思路。';});
    };
    const repos = []; const pricing = { 'gpt-research': { input: 2, output: 8, cache_read: 0.2, cache_write: 2.5 } };
    let features = {science_local_mcp:true, science_zh:false};
    window.testCalls = []; window.__TAURI__ = { core: { invoke: async (cmd, args = {}) => {
      window.testCalls.push({ cmd, args });
      if (cmd === 'get_config') return { ...features, provider: 'test', mode: 'proxy', proxy_port: 18992, sandbox_port: 8991, connections: [{ id: 'test', name: '研究 API', protocol: 'responses', model: 'gpt-research', base_url: 'https://example.com', has_key: true }] };
      if (cmd === 'set_science_features') { features={science_local_mcp:args.localMcp, science_zh:args.zh};return {message:'插件设置已保存。'}; }
      if (cmd === 'status') return { proxy: 'amber', sandbox: 'amber' };
      if (cmd === 'read_usage') return { entries: Array.from({ length: 31 }, (_, i) => ({ time: now - i * 3600, provider: i === 30 ? 'other' : 'test', model: i === 30 ? 'unpriced' : 'gpt-research', protocol: 'responses', input_tokens: 1000, output_tokens: 250, cache_read_input_tokens: 400, latency_ms: 850, ok: i !== 1 })) };
      if (cmd === 'mcp_manage') {
        const { operation: op, params: p } = args;
        if (offline && op.startsWith('skills_')) throw new Error('请先启动第三方 Science。');
        if (op === 'skills_list') return { skills: structuredClone(skills), degraded: Boolean(window.catalogDegraded), localImportAvailable: Boolean(window.localImportAvailable), customAllowed: !window.customSkillsBlocked };
        if (op === 'skills_content') return { content: '---\nname: ' + p.name + '\n---\n<unsafe-content>\n# Methods' };
        if (op === 'skills_enabled') skills.find(s => s.name === p.name).enabled = p.enabled;
        if (op === 'skills_update') { if(window.updateFails) throw new Error('Science 验证失败，已恢复旧内容。'); skills.find(s=>s.name===p.name).fingerprint='new-content'; }
        if (op === 'skills_remove') skills = skills.filter(s => s.name !== p.name);
        if (op === 'skills_import') { if (window.importFails || window.failFile === p.filename) throw new Error('Science HTTP 409：同名技能已存在。'); skills.push({ name: p.filename.replace(/\.zip|\.md/g, ''), source: window.catalogDegraded ? 'csswitch-local' : 'local', description: '新技能' }); return { name:p.filename.replace(/\.(zip|skill|md)$/, ''),message: 'Skill 已导入 Science。' }; }
        if (op === 'list') return { connectors: structuredClone(connectors), customAllowed: true };
        if (op === 'add_local' || op === 'add_remote') connectors.push({ id: 'local:' + p.name, name: p.name, source: op === 'add_local' ? 'local-stdio' : 'custom', transport: op === 'add_local' ? 'stdio' : p.transport, attachedAgents: p.attach ? ['OPERON'] : [] });
        return { message: '已更新。' };
      }
      if (cmd === 'workspace_manage') {
        const { operation: op, params: p } = args;
        if (op === 'skills_discover') return { skills: [{ key: 'codex:paper-reader', name: 'paper-reader', directory: 'paper-reader', source: 'codex', description: '已安装技能', path: '~/.codex/skills/paper-reader' }, { key: 'agents:lab-notes', name: 'lab-notes', directory: 'lab-notes', source: 'agents', description: '整理实验笔记', path: '~/.agents/skills/lab-notes' }], repos: structuredClone(repos) };
        if (op === 'skill_bundle') return { filename: p.key.split(':').pop() + '.zip', bundle: 'dGVzdA==', sourceKey:p.key, fingerprint:'new-content' };
        if (op === 'skill_import_plan') return { skills:[{name:p.filename.replace(/\.(zip|skill|md)$/, ''),filename:p.filename,bundle:p.bundle}] };
        if (op === 'repo_add') { if (window.repoFails) throw new Error('无法下载 GitHub 仓库。'); repos.push({ key: p.repo + '@' + p.branch, repo: p.repo, branch: p.branch }); }
        if (op === 'repo_remove') repos.splice(repos.findIndex(r => r.key === p.key), 1);
        if (op === 'sessions_list') return { sessions: structuredClone(sessions) };
        if (op === 'session_meta') Object.assign(sessions.find(s => s.key === p.key), p.changes);
        if (op === 'session_messages') {
          if(window.timelineFixture) return {total:window.timelineFixture.length,has_more:false,messages:structuredClone(window.timelineFixture)};
          const many = p.key === 'org:session-0', total = many ? 101 : 1;
          return { total, offset: p.offset, has_more: many && p.offset === 0, messages: Array.from({ length: many && p.offset === 0 ? 100 : 1 }, (_, i) => {
            const idx=p.offset+i, text=idx===0 ? '请比较 AlphaFold 预测结构和实验结构，整理一份验证方案。' : idx===1 ? window.readerFixture || '## 结构验证方案\n\n先检查预测置信度，再结合实验数据验证关键位点。\n\n- **整体结构**：比较 TM-score 和主链 RMSD。\n- **局部区域**：检查 pLDDT 与 PAE。\n- **实验验证**：优先选择结合位点与低置信度区域。\n\n```python\nfrom Bio.PDB import PDBParser\nstructure = PDBParser().get_structure("target", "target.pdb")\n```\n\n| 指标 | 用途 |\n| --- | --- |\n| pLDDT | 局部置信度 |\n| PAE | 域间相对位置 |' : '<unsafe-message>\n研究方法 '+(idx+1);
            return {idx,role:i%2?'assistant':'user',text,...(idx===1?{blocks:[{type:'text',text},{type:'tool_use',name:'PubMed',text:'{"query":"protein structure validation"}'},{type:'tool_result',text:'检索完成：找到 12 篇相关文献。'}]}:{})};
          }) };
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
  await page.getByRole('button',{name:'开始批量导入',exact:true}).click();
  await expect(page.locator('#notice')).toContainText('409');
  await page.evaluate(() => { window.importFails = false; }); await page.locator('#skillFile').setInputFiles({ name: 'new-skill.md', mimeType: 'text/markdown', buffer: Buffer.from('---\nname: new-skill\n---\n') });
  await page.getByRole('button',{name:'开始批量导入',exact:true}).click();
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
  await page.evaluate(()=>window.useDesignPreview());
  for (const [width, height] of [[1200, 820], [980, 720], [390, 844]]) {
    await page.setViewportSize({ width, height });
    for (const [label, id] of [['MCP 管理', 'mcp'], ['Skills 管理', 'skills'], ['用量统计', 'usage'], ['会话管理', 'sessions']]) {
      await page.getByRole('button', { name: label, exact: true }).click(); await expect(page.locator('#content')).toHaveAttribute('aria-busy', 'false');
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: `../artifacts/screenshots/${id}-workspace-${width}.png`, fullPage: true });
      if(id==='sessions'){
        await page.getByRole('button',{name:'查看 蛋白质结构研究',exact:true}).first().click();
        await expect(page.locator('#sessionReader')).toBeVisible();
        expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
        await page.screenshot({path:`../artifacts/screenshots/session-reader-${width}.png`,fullPage:true});
        await page.getByRole('button',{name:'关闭会话阅读',exact:true}).click();
      }
    }
  }
  await page.getByRole('button',{name:'查看 蛋白质结构研究',exact:true}).first().click(); await expect(page.locator('#sessionReader')).toContainText('蛋白质结构研究'); expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole('button', { name: '切换深浅主题' }).click(); await page.screenshot({ path: '../artifacts/screenshots/session-reader-dark.png', fullPage: true });
  await expect(page.locator('unsafe-skill, unsafe-session, unsafe-message')).toHaveCount(0); expect(errors).toEqual([]);
});

test('Skills batch selection respects policy and keeps successful operations when another item is skipped', async ({ page }) => {
  await setup(page); await page.getByRole('button', {name:'Skills 管理',exact:true}).click();
  await page.getByLabel('全选当前页 Skills',{exact:true}).check();
  await expect(page.getByLabel('选择 blocked',{exact:true})).toBeDisabled();
  await expect(page.locator('#skillBatchBar')).toContainText('已选 2 个');
  await page.getByRole('button',{name:'批量停用',exact:true}).click();
  await expect(page.getByRole('switch',{name:'启用 paper-reader',exact:true})).toHaveAttribute('aria-checked','false');
  await expect(page.getByRole('switch',{name:'启用 data-analysis',exact:true})).toHaveAttribute('aria-checked','false');
  await page.getByLabel('全选当前页 Skills',{exact:true}).check();
  await page.getByRole('button',{name:'卸载所选',exact:true}).click(); await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.locator('#skillRows')).not.toContainText('paper-reader');
  await expect(page.locator('#skillRows')).toContainText('data-analysis');
  await expect(page.locator('#skillReport')).toContainText('受组织策略或来源限制');
});

test('batch file import reports individual failures and continues to import the next skill', async ({ page }) => {
  await setup(page); await page.getByRole('button',{name:'Skills 管理',exact:true}).click();
  await page.evaluate(()=>{window.failFile='first.md';});
  await page.locator('#skillFile').setInputFiles(['first','second'].map(name=>({name:name+'.md',mimeType:'text/markdown',buffer:Buffer.from(`---\nname: ${name}\ndescription: Test\n---\n`)})));
  await expect(page.getByRole('heading',{name:'导入 2 个 Skills 文件'})).toBeVisible();
  await page.getByRole('button',{name:'开始批量导入',exact:true}).click();
  await expect(page.locator('#skillReport')).toContainText('first'); await expect(page.locator('#skillReport')).toContainText('409');
  await expect(page.locator('#skillReport')).toContainText('second'); await expect(page.locator('#skillRows')).toContainText('second');
});

test('check and update a managed skill preserves its disabled state and original source', async ({ page }) => {
  await setup(page); await page.evaluate(()=>{window.addManagedFixture(); window.catalogDegraded=true; window.localImportAvailable=true;});
  await page.getByRole('button',{name:'Skills 管理',exact:true}).click();
  await page.getByRole('button',{name:'检查更新',exact:true}).click();
  await expect(page.locator('#content')).toContainText('1 个可更新');
  await page.getByLabel('Skills 状态').selectOption('updates'); await expect(page.locator('.skill-row')).toHaveCount(1);
  await page.getByRole('button',{name:'全部更新',exact:true}).click(); await page.getByRole('button',{name:'确认',exact:true}).click();
  await expect(page.locator('#skillReport')).toContainText('1 项完成');
  await page.getByLabel('Skills 状态').selectOption('all');
  await expect(page.getByRole('switch',{name:'启用 lab-notes',exact:true})).toHaveAttribute('aria-checked','false');
  const update=await page.evaluate(()=>window.testCalls.find(c=>c.args.operation==='skills_update').args.params);
  expect(update.sourceKey).toBe('agents:lab-notes'); expect(update.expectedFingerprint).toBe('old-content');
  await page.getByRole('button',{name:'检查更新',exact:true}).click(); await expect(page.locator('#notice')).toContainText('0 个技能可更新');
});

test('session reader opens from the full-width list, renders Markdown, filters tools, searches loaded content and navigates outline', async ({ page }) => {
  await setup(page); await page.getByRole('button',{name:'会话管理',exact:true}).click();
  await page.getByRole('button',{name:'查看 蛋白质结构研究',exact:true}).click();
  await expect(page.locator('.markdown h2')).toContainText('结构验证方案'); await expect(page.locator('.markdown table')).toHaveCount(1);
  await expect(page.getByRole('button',{name:'复制代码',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'工具',exact:true}).click();
  await expect(page.locator('.message')).toHaveCount(1); await expect(page.locator('.message-step')).toHaveCount(1);
  await page.locator('.message-step').first().locator('summary').click(); await expect(page.locator('.message-step').first()).toContainText('protein structure validation');
  await page.getByRole('button',{name:'对话',exact:true}).click(); await expect(page.locator('.message-step')).toHaveCount(0);
  await page.getByRole('button',{name:'搜索当前会话',exact:true}).click();
  await page.getByLabel('搜索会话内容').fill('结构验证方案'); await expect(page.locator('.message')).toHaveCount(2);
  await page.getByLabel('搜索会话内容').fill(''); await page.getByRole('button',{name:'切换对话目录',exact:true}).click();
  await expect(page.locator('#sessionOutline')).toBeVisible(); await page.locator('[data-message-jump]').nth(4).click();
  await expect(page.locator('unsafe-message, unsafe-session, unsafe-skill, .markdown script, .markdown img')).toHaveCount(0);
});

test('sidebar collapse persists and navigation has exactly one active page', async ({ page }) => {
  await setup(page); await page.getByRole('button',{name:'收起导航',exact:true}).click();
  await expect(page.locator('html')).toHaveAttribute('data-sidebar','collapsed');
  await page.getByRole('button',{name:'Skills 管理',exact:true}).click();
  await expect(page.locator('.nav-item.active')).toHaveCount(1); await expect(page.locator('.nav-item.active')).toHaveText('Skills 管理');
  await page.reload(); await expect(page.getByRole('button',{name:'展开导航',exact:true})).toBeVisible();
});

test('manually imported skills update from new files only when the user selects the update option', async ({page})=>{
  await setup(page); await page.evaluate(()=>{window.addManagedFixture(true);window.catalogDegraded=true;window.localImportAvailable=true;});
  await page.getByRole('button',{name:'Skills 管理',exact:true}).click();
  await page.locator('#skillFile').setInputFiles({name:'lab-notes.md',mimeType:'text/markdown',buffer:Buffer.from('---\nname: lab-notes\ndescription: Updated\n---\n')});
  await page.getByLabel('更新通过文件导入的同名本地技能').check();
  await page.getByRole('button',{name:'开始批量导入',exact:true}).click();
  await expect(page.locator('#skillReport')).toContainText('1 项完成');
  const update=await page.evaluate(()=>window.testCalls.find(c=>c.args.operation==='skills_update').args.params);
  expect(update.name).toBe('lab-notes'); expect(update.expectedFingerprint).toBe('old-content');
  await expect(page.getByRole('switch',{name:'启用 lab-notes',exact:true})).toHaveAttribute('aria-checked','false');
});

test('untrusted Markdown cannot execute HTML, retain unsafe URLs or load remote images', async ({page})=>{
  const remote=[];page.on('request',r=>{if(r.url().includes('tracking.example'))remote.push(r.url());});
  await setup(page);await page.evaluate(()=>{window.readerFixture='<img src="https://tracking.example/pixel" onerror="window.readerExecuted=true">\n\n<script>window.readerExecuted=true</script>\n\n[unsafe](javascript:window.readerExecuted=true)\n\n![remote image](https://tracking.example/image.png)';});
  await page.getByRole('button',{name:'会话管理',exact:true}).click();
  await page.getByRole('button',{name:'查看 蛋白质结构研究',exact:true}).click();
  await expect(page.locator('#sessionReader')).toContainText('remote image');
  await expect(page.locator('.markdown img, .markdown script, .markdown [onerror], .markdown a[href^="javascript:"]')).toHaveCount(0);
  expect(await page.evaluate(()=>Boolean(window.readerExecuted))).toBe(false);expect(remote).toEqual([]);
});

test('cc-switch session list groups projects and opens a full-window reader without loading content in the list', async ({page})=>{
  await setup(page);await page.getByRole('button',{name:'会话管理',exact:true}).click();
  await expect(page.locator('.sessions-layout')).toHaveCount(0);await expect(page.locator('#sessionReader')).toHaveCount(0);
  expect(await page.evaluate(()=>window.testCalls.filter(c=>c.args.operation==='session_messages').length)).toBe(0);
  await expect(page.locator('.session-group')).toHaveCount(2);await expect(page.locator('.session-group').first()).toHaveAttribute('open','');
  await expect(page.locator('.session-group').nth(1)).not.toHaveAttribute('open','');
  await page.locator('.session-group').nth(1).locator('summary').click();await expect(page.getByRole('button',{name:'查看 <unsafe-session>',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'按时间',exact:true}).click();await expect(page.locator('.session-row:visible')).toHaveCount(25);
  await page.getByLabel('搜索会话',{exact:true}).fill('session-0');await expect(page.locator('.session-row')).toHaveCount(1);
  await page.getByRole('button',{name:'查看 蛋白质结构研究',exact:true}).click();await expect(page.locator('.sidebar')).toBeHidden();
  await expect(page.locator('.session-browser')).toHaveCount(0);
  expect(await page.locator('#sessionReader').evaluate(e=>e.getBoundingClientRect().width>=innerWidth-2)).toBe(true);
  await page.getByRole('button',{name:'关闭会话阅读',exact:true}).click();await expect(page.getByLabel('搜索会话',{exact:true})).toHaveValue('session-0');
  await expect(page.locator('.session-row')).toHaveCount(1);await expect(page.locator('.sidebar')).toBeVisible();
});

test('session execution timeline pairs tool results, preserves failures, expands long output and filters changes', async ({page})=>{
  await setup(page);await page.setViewportSize({width:1200,height:820});await page.evaluate(()=>{window.timelineFixture=[
    {idx:0,role:'user',text:'请检查这份单细胞数据分析脚本，修复输入检查，并整理一份可复现的运行说明。',timestamp:Date.now()},
    {idx:1,role:'assistant',blocks:[{type:'thinking',text:'先检查脚本和输入。'},{type:'tool_use',id:'call-1',name:'Bash',text:'{"command":"python analysis.py"}'}]},
    {idx:2,role:'user',blocks:[{type:'tool_result',tool_use_id:'call-1',is_error:true,text:Array.from({length:40},(_,i)=>'error line '+i).join('\n')}]},
    {idx:3,role:'assistant',blocks:[{type:'tool_use',id:'call-2',name:'Edit',text:'{"file_path":"analysis.py","old_string":"old","new_string":"new"}'}]},
    {idx:4,role:'user',blocks:[{type:'tool_result',tool_use_id:'call-2',text:'文件已更新'}]},
    {idx:5,role:'assistant',text:'## 更新完成\n\n已修复输入检查。'}
  ];});
  await page.getByRole('button',{name:'会话管理',exact:true}).click();await page.getByRole('button',{name:'查看 蛋白质结构研究',exact:true}).click();
  await expect(page.locator('.message.user')).toHaveCount(1);await expect(page.locator('.execution-timeline')).toHaveCount(1);
  await expect(page.locator('#readerStats')).toContainText('1 次提问 · 2 次工具调用');
  await expect(page.locator('.execution-timeline')).toContainText('1 个失败');await expect(page.locator('.timeline-step')).toHaveCount(3);
  const failed=page.locator('.timeline-step.failed');await failed.locator('summary').click();await expect(failed).toContainText('error line 0');
  await expect(failed.locator('pre').last()).not.toContainText('error line 39');
  await page.screenshot({path:'../artifacts/screenshots/session-reader-execution-1200.png',fullPage:true});
  await failed.getByRole('button',{name:'显示全部 · 40 行',exact:true}).click();
  await expect(page.locator('.timeline-step.failed pre').last()).toContainText('error line 39');
  await page.getByRole('button',{name:'改动',exact:true}).click();await expect(page.locator('.timeline-step')).toHaveCount(1);
  await expect(page.locator('.timeline-step summary')).toContainText('Edit');await page.locator('.timeline-step summary').click();await expect(page.locator('.timeline-step')).toContainText('文件已更新');
  await page.getByRole('button',{name:'对话',exact:true}).click();await expect(page.locator('.execution-timeline')).toHaveCount(0);await expect(page.locator('.markdown h2')).toHaveText('更新完成');
  await page.evaluate(()=>{window.timelineFixture[5].text='## 刷新后的回复\n\n继续验证。';});
  await page.getByRole('button',{name:'刷新当前会话',exact:true}).click();await expect(page.locator('.markdown h2')).toHaveText('刷新后的回复');
});

test('Science plugin switches persist independently and stay reversible', async ({ page }) => {
  await setup(page); await page.getByRole('button',{name:'设置',exact:true}).click();
  await expect(page.getByRole('switch',{name:'使用本机 MCP'})).toHaveCount(0);
  await expect(page.getByRole('switch',{name:'Science 页面汉化'})).toHaveAttribute('aria-checked','false');
  await page.getByRole('switch',{name:'Science 页面汉化'}).click();
  await expect(page.getByRole('switch',{name:'Science 页面汉化'})).toHaveAttribute('aria-checked','true');
  await page.getByRole('button',{name:'MCP 管理',exact:true}).click();
  await expect(page.getByRole('switch',{name:'使用本机 MCP'})).toHaveAttribute('aria-checked','true');
  await page.getByRole('switch',{name:'使用本机 MCP'}).click();
  await expect(page.getByRole('switch',{name:'使用本机 MCP'})).toHaveAttribute('aria-checked','false');
  await page.getByRole('button',{name:'设置',exact:true}).click();
  await expect(page.getByRole('switch',{name:'Science 页面汉化'})).toHaveAttribute('aria-checked','true');
  await page.getByRole('switch',{name:'Science 页面汉化'}).click();
  await expect(page.getByRole('switch',{name:'Science 页面汉化'})).toHaveAttribute('aria-checked','false');
});


test('Claude hosted MCP blocked by launch policy is clearly labeled and cannot be enabled', async ({ page }) => {
  await setup(page); await page.evaluate(()=>window.addHostedFixture());
  await page.getByRole('button',{name:'MCP 管理',exact:true}).click();
  const row=page.locator('[data-mcp-id="bundled:pubmed"]');
  await expect(row).toContainText('第三方策略停用');
  await expect(row.getByRole('button',{name:'启用',exact:true})).toBeDisabled();
  await expect(page.locator('[data-mcp-id="local:memory"]').getByRole('button',{name:'解绑 Science',exact:true})).toBeEnabled();
});
