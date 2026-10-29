import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
const plugin = readFileSync(new URL('../../scripts/science-zh.js', import.meta.url), 'utf8');

test('online UI translations batch, protect research content, and use cached translations offline', async ({page}) => {
  let online = true, calls = 0;
  await page.route('**/__csswitch/config', route=>route.fulfill({json:{enabled:true,online,translationToken:'fixture-token',translationRevision:'1'}}));
  await page.route('**/__csswitch/zh/cache', route=>route.fulfill({json:{translations:{'Cached interface label':'已缓存的界面标签'},revision:'1'}}));
  await page.route('**/__csswitch/zh/translate', async route=>{
    calls++;
    expect(route.request().headers()['x-csswitch-translation']).toBe('fixture-token');
    expect(route.request().postDataJSON()).toEqual({texts:['Future interface label']});
    await route.fulfill({json:{translations:{'Future interface label':'新的界面标签'},skipped:[],revision:'1'}});
  });
  await page.goto('/');
  await page.setContent('<nav><button>Future interface label</button><button>Cached interface label</button><button>More models</button><span>This week</span><button>Where you left off</button><button data-testid="session-title">Private project title</button></nav><section class="markdown-content"><h2>Private research heading</h2></section><input value="Secret research input"><div contenteditable="true">Private editor</div>');
  await page.addScriptTag({content:plugin});
  await expect(page.locator('nav button').nth(0)).toHaveText('新的界面标签');
  await expect(page.locator('nav button').nth(1)).toHaveText('已缓存的界面标签');
  await expect(page.locator('nav button').nth(2)).toHaveText('更多模型');
  await expect(page.locator('nav span')).toHaveText('本周');
  await expect(page.locator('nav button').nth(3)).toHaveText('上次阅读位置');
  await expect(page.getByTestId('session-title')).toHaveText('Private project title');
  await expect(page.locator('.markdown-content')).toHaveText('Private research heading');
  await expect(page.locator('input')).toHaveValue('Secret research input');
  online = false;
  await expect.poll(()=>page.evaluate(()=>window.__csswitchZh.audit().online)).toBe(false);
  await page.evaluate(()=>{const b=document.createElement('button');b.textContent='Cached interface label';document.querySelector('nav').append(b);});
  await expect(page.locator('nav button').last()).toHaveText('已缓存的界面标签');
  expect(calls).toBe(1);
  await page.evaluate(()=>window.__csswitchZh.setEnabled(false));
  await expect(page.locator('nav button').first()).toHaveText('Future interface label');
});

test('disabling translation during a network request never applies a late result', async ({page}) => {
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true,online:true,translationToken:'token'}}));
  let received;
  const started = new Promise(resolve=>{ received=resolve; });
  let release;
  const response = new Promise(resolve=>{ release=resolve; });
  await page.route('**/__csswitch/zh/translate',async route=>{received();await response;await route.fulfill({json:{translations:{'Future interface label':'新的界面标签'}}}).catch(()=>{});});
  await page.goto('/');await page.setContent('<button>Future interface label</button>');await page.addScriptTag({content:plugin});
  await started;await page.evaluate(()=>window.__csswitchZh.setEnabled(false));release();
  await expect(page.locator('button')).toHaveText('Future interface label');
});

test('online translation registers native assets even when the browser had already loaded them', async ({page}) => {
  let assetReads=0, translated=false;
  await page.route('**/assets/native-fixture.js',route=>{assetReads++;return route.fulfill({contentType:'application/javascript',body:'window.fixtureNativeLoaded=true'});});
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true,online:true,translationToken:'token'}}));
  await page.route('**/__csswitch/zh/translate',route=>{
    expect(assetReads).toBe(2);translated=true;
    return route.fulfill({json:{translations:{'Future interface label':'新的界面标签'}}});
  });
  await page.goto('/');await page.setContent('<button>Future interface label</button>');
  await page.addScriptTag({url:'/assets/native-fixture.js'});
  await page.addScriptTag({content:plugin});
  await expect(page.locator('button')).toHaveText('新的界面标签');
  expect(translated).toBe(true);
});

test('artifact provenance menus translate fully and restore without changing document text', async ({page}) => {
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<section class="markdown-content"><h2>Provenance</h2><p>View in context</p></section>');
  await page.addScriptTag({content:plugin});
  await page.evaluate(()=>{
    const menu=document.createElement('div');menu.setAttribute('role','menu');menu.setAttribute('aria-label','Artifact actions');
    menu.innerHTML='<button role="menuitem" title="View provenance"><span aria-hidden="true">↗</span><span>Provenance</span></button><button role="menuitem">View in context</button><button role="menuitem">Open in Artifact Viewer</button>';
    document.body.append(menu);
  });
  await expect(page.getByRole('menu')).toHaveAttribute('aria-label','文件操作');
  await expect(page.getByRole('menuitem').nth(0)).toContainText('来源追溯');
  await expect(page.getByRole('menuitem').nth(0)).toHaveAttribute('title','查看来源追溯');
  await expect(page.getByRole('menuitem').nth(1)).toHaveText('查看上下文');
  await expect(page.getByRole('menuitem').nth(2)).toHaveText('在产物预览中打开');
  await expect(page.locator('.markdown-content h2')).toHaveText('Provenance');
  await expect(page.locator('.markdown-content p')).toHaveText('View in context');
  await page.evaluate(()=>window.__csswitchZh.setEnabled(false));
  await expect(page.getByRole('menu')).toHaveAttribute('aria-label','Artifact actions');
  await expect(page.getByRole('menuitem').nth(0)).toHaveAttribute('title','View provenance');
  await expect(page.getByRole('menuitem').nth(1)).toHaveText('View in context');
});

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

test('settings portals translate without animation frames and restore options and descriptions', async ({ page }) => {
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<main><p class="markdown-content">Reasoning effort</p></main>');
  await page.evaluate(()=>window.requestAnimationFrame=()=>0);
  await page.addScriptTag({content:plugin});
  await page.evaluate(()=>{
    const dialog=document.createElement('section');dialog.setAttribute('role','dialog');
    dialog.innerHTML='<h2>Settings</h2><nav>Skills Connectors</nav><p>Where Claude Science keeps your projects, files, and history on this machine</p><button>All (24)</button><button aria-label="Copy organization ID fixture-id">Copy</button><select><option>Same as main model</option></select><input placeholder="Search" value="My English skill"><p>https://example.org/private</p>';
    document.body.append(dialog);
  });
  await expect(page.locator('h2')).toHaveText('设置');
  await expect(page.locator('[role=dialog] p').first()).toContainText('保存项目、文件和历史记录的位置');
  await expect(page.locator('button').first()).toHaveText('全部（24）');
  await expect(page.locator('button').nth(1)).toHaveAttribute('aria-label','复制组织 ID fixture-id');
  await expect(page.locator('option')).toHaveText('与主模型相同');
  await expect(page.locator('input')).toHaveValue('My English skill');
  await expect(page.locator('.markdown-content')).toHaveText('Reasoning effort');
  await expect(page.locator('[role=dialog] p').last()).toHaveText('https://example.org/private');
  await page.evaluate(()=>window.__csswitchZh.setEnabled(false));
  await expect(page.locator('option')).toHaveText('Same as main model');
  await expect(page.locator('[role=dialog] p').first()).toHaveText('Where Claude Science keeps your projects, files, and history on this machine');
});

test('native split settings wording and provider forms translate while editable content remains original',async({page})=>{
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<section role="dialog" data-testid="settings-modal"><p><span>Max</span> <span>plan</span></p><p>No session activity in the <span>last 7 days</span>.</p><button title="Expand Package management" aria-label="Model: Same as main model">Same as main model</button><label>Service account JSON</label><p>Claude sees this as an environment variable with this name</p><textarea placeholder="Optional — leave empty to use Claude Science\'s prompt as-is.">My unchanged English instructions</textarea><p class="markdown-content">No session activity in the last 7 days.</p></section><header>plan</header>');
  await page.addScriptTag({content:plugin});
  await expect(page.locator('section p').nth(0)).toHaveText('Max 套餐');
  await expect(page.locator('section p').nth(1)).toContainText('过去 7 天');
  await expect(page.locator('button')).toHaveAttribute('aria-label','模型：与主模型相同');
  await expect(page.locator('button')).toHaveAttribute('title','展开软件包管理');
  await expect(page.locator('label')).toHaveText('服务账号 JSON');
  await expect(page.locator('textarea')).toHaveAttribute('placeholder','可选，留空以使用 Claude Science 原有的提示词。');
  await expect(page.locator('textarea')).toHaveValue('My unchanged English instructions');
  await expect(page.locator('.markdown-content')).toHaveText('No session activity in the last 7 days.');
  await expect(page.locator('header')).toHaveText('plan');
  await page.evaluate(()=>window.__csswitchZh.setEnabled(false));
  await expect(page.locator('section p').first()).toHaveText('Max plan');
  await expect(page.locator('button')).toHaveAttribute('aria-label','Model: Same as main model');
});

test('populated permission records translate and preserve tool and host identifiers',async({page})=>{
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<section role="dialog" data-testid="settings-modal"><h2>Permissions</h2><section><h3>Local compute</h3><p>Sandbox tools that run without preview</p><div><span>python</span> <span>any call</span></div><div><span>manage_packages:shared</span> <span>any call</span></div><button>Revoke all</button></section><section><h3>Compute</h3><p>Remote hosts where jobs run without preview</p><div><span>neiwang</span> <span>any command</span></div><button>Revoke all</button></section></section>');
  await page.addScriptTag({content:plugin});
  await expect(page.locator('h3').first()).toHaveText('本地计算');
  await expect(page.locator('p').first()).toHaveText('无需预览即可运行的沙箱工具');
  await expect(page.locator('p').last()).toHaveText('无需预览即可运行任务的远程主机');
  await expect(page.locator('span').filter({hasText:'任意调用'})).toHaveCount(2);
  await expect(page.locator('span').last()).toHaveText('任意命令');
  for(const name of ['python','manage_packages:shared','neiwang']) await expect(page.getByText(name,{exact:true})).toHaveText(name);
  await page.evaluate(()=>window.__csswitchZh.setEnabled(false));
  await expect(page.locator('h3').first()).toHaveText('Local compute');
  await expect(page.locator('span').last()).toHaveText('any command');
});

test('permission revoke confirmation translates the alertdialog description and restores it',async({page})=>{
  await page.route('**/__csswitch/config',route=>route.fulfill({json:{enabled:true}}));
  await page.goto('/');
  await page.setContent('<section role="alertdialog" data-cds="ConfirmationDialog"><h2>Revoke all local compute grants?</h2><p>The 2 allowed local compute grants shown will be removed. Blocked and ask-each-time rules are kept.</p><button>Cancel</button></section>');
  await page.addScriptTag({content:plugin});
  await expect(page.locator('h2')).toHaveText('撤销全部本地计算授权？');
  await expect(page.locator('p')).toHaveText('将移除当前显示的 2 条本地计算允许规则。禁止和每次询问的规则会保留。');
  await page.evaluate(()=>window.__csswitchZh.setEnabled(false));
  await expect(page.locator('p')).toHaveText('The 2 allowed local compute grants shown will be removed. Blocked and ask-each-time rules are kept.');
});


test('all connector names retain English while surrounding controls translate', async ({ page }) => {
  await page.route('**/__csswitch/config', route => route.fulfill({ json: { enabled: true } }));
  await page.goto('/');
  const names = ['BioMart','Cancer Models','CellGuide','Chemistry','Clinical Genomics','Drug Regulatory','Expression','Genes & Ontologies','Genomes','Human Genetics','Ketcher Chemistry','Literature Graph','Omics Archives','Protein Annotation','Regulation','Research Resources','RNA','Structures & Interactions','Variants','ZINC','bioRxiv','ChEMBL','Clinical Trials','PubMed'];
  await page.setContent('<section role="dialog"><h2>Connectors</h2>' + names.map(name => `<article><h3>${name}</h3><button aria-label="Enable ${name}">Enable</button></article>`).join('') + '</section>');
  await page.addScriptTag({ content: plugin });
  await expect(page.locator('h2')).toHaveText('连接器');
  await expect(page.locator('h3')).toHaveText(names);
  for (let i = 0; i < names.length; i++) await expect(page.locator('button').nth(i)).toHaveAttribute('aria-label', '启用' + names[i]);
});
