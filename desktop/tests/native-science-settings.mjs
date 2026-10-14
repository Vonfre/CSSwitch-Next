import { chromium } from 'playwright';
import { readFileSync, writeFileSync } from 'node:fs';
import assert from 'node:assert/strict';

const { url, config, root } = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const collect = process.argv.includes('--collect');
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  // Render nonempty permission categories through the native UI without
  // creating real grants in either the fixture daemon or the user's instance.
  const permissionFixtures = [
    ['local_exec','python'],['local_exec','manage_packages:shared'],['remote_exec','ssh:fixture-host'],
    ['host','ro:/fixture/read-only'],['host','rw:/fixture/writable'],['host_delete','/fixture/deletable'],
    ['artifact_delete','artifact-a\u0000artifact-b'],['mcp_tool','fixture-connector\u0000*'],
    ['customize_mutation','agent_create'],['remote_read','ssh:fixture-host\u0000/fixture/downloads'],
    ['credential_access','FIXTURE_API_KEY'],['byoc_submit','fixture-cloud'],
    ['byoc_kernel','fixture-cloud'],['infer_submit','fixture-model'],
  ].map(([kind,key])=>({kind,key,scope:'always',tier:'allow',serverDisplayName:'Fixture MCP'}));
  await page.route(/\/api\/approvals\/grants(?:\?.*)?$/,async route=>{
    assert.equal(route.request().method(),'GET','Permission probe must never modify grants');
    const kind=new URL(route.request().url()).searchParams.get('kind');
    await route.fulfill({json:{grants:kind?permissionFixtures.filter(g=>g.kind===kind):permissionFixtures}});
  });
  await page.goto(url);
  const signIn = page.locator('form[action*="/api/auth/nonce"] button');
  if (await signIn.count()) await signIn.click();
  await page.waitForTimeout(2000);
  // Inspect the native shipped UI code and settings DOM, using only the isolated
  // fixture's authentication. Never read the user's Science browser cookies.
  const source = await page.locator('script[src*="/assets/index-"]').getAttribute('src');
  const main = await (await page.request.get(new URL(source, page.url()).href)).text();
  const chunks = [...main.matchAll(/\.\/((?:ControlPanel|Capabilities)[^"/]+\.js)/g)].map(m => m[1]);
  for (const name of new Set(chunks)) {
    const response = await page.request.get(new URL('/assets/' + name, page.url()).href);
    assert.equal(response.status(), 200);
    writeFileSync(root + '/' + name, await response.text());
    const text=await response.text();
    const dependencies=[...text.matchAll(/\.\/([^"/]+\.js)/g)].map(m=>m[1]).filter(n=>/^(index-|MemoryScreen|CloudStorageModal|useCloudCredentials|providers-|primitives-)/.test(n));
    for(const dependency of new Set(dependencies)){
      const asset=await page.request.get(new URL('/assets/'+dependency,page.url()).href);
      assert.equal(asset.status(),200);writeFileSync(root+'/'+dependency,await asset.text());
    }
  }
  await page.evaluate(() => window.__csswitchZh.setEnabled(false));
  writeFileSync(config, '{"science_zh":false}');
  await page.getByRole('button', { name: /^Account menu/ }).click();
  await page.getByRole('menuitem', { name: /^(Settings|设置)$/ }).click();
  await page.waitForTimeout(500);
  const original = {}, modules = ['Skills', 'Connectors', 'Specialists', 'Memory', 'Compute', 'Network', 'Permissions', 'Credentials', 'Storage', 'Usage', 'General'];
  // Disable the dictionary while collecting the actual native wording.
  await page.evaluate(() => window.__csswitchZh.setEnabled(false));
  writeFileSync(config, '{"science_zh":false}');
  for (const name of modules) {
    await page.getByRole('button', { name, exact: true }).click();
    await page.waitForTimeout(600);
    const panel = page.locator('body');
    original[name] = {
      text: await panel.innerText(),
      controls: await panel.locator('button,input,textarea,[aria-label],[title]').evaluateAll(nodes => nodes.map(n => ({ text: n.textContent, label: n.getAttribute('aria-label'), title: n.getAttribute('title'), placeholder: n.getAttribute('placeholder') }))),
    };
  }
  original.dom = await page.locator('body').evaluate(n => n.outerHTML);
  writeFileSync(root + '/settings-original.json', JSON.stringify(original, null, 2));
  assert.equal(errors.length, 0, errors.join(';'));
  if (collect) console.log('Collected all 11 native Science settings modules and shipped source chunks.');
  else {
    await page.evaluate(() => window.__csswitchZh.setEnabled(true));
    writeFileSync(config, '{"science_zh":true}');
    const translated = {}, ids = {Specialists:'agents'};
    const expected = {
      Skills:['技能','从 GitHub 添加的技能'], Connectors:['连接器','你添加的连接器'],
      Specialists:['专家','审阅专家'], Memory:['记忆','关于你'], Compute:['计算资源','SSH 主机'],
      Network:['网络','代理地址'], Permissions:['权限','全部（14）','本地计算','无需预览即可运行的沙箱工具','任意调用','任意命令','远程文件','云计算','读取值','服务商内核','发送到模型服务'], Credentials:['凭据','服务'],
      Storage:['存储','磁盘用量'], Usage:['用量','本地估算'], General:['通用','推理强度','诊断'],
    };
    const capture = async name => {
      await page.waitForTimeout(250);
      translated[name] = await page.locator('[role="dialog"],[role="alertdialog"],[role="menu"],[role="listbox"]').evaluateAll(nodes => nodes.map(n => ({
        text:n.innerText,
        english:[...function*(){const w=document.createTreeWalker(n,NodeFilter.SHOW_TEXT);let t;while(t=w.nextNode())if(t.parentElement?.checkVisibility()&&/[A-Za-z]{2}/.test(t.nodeValue))yield t.nodeValue.trim();}()],
        controls:[...n.querySelectorAll('[aria-label],[title],[placeholder]')].map(e=>({label:e.getAttribute('aria-label'),title:e.getAttribute('title'),placeholder:e.getAttribute('placeholder')})),
      })));
      writeFileSync(root+'/settings-translated.json', JSON.stringify(translated,null,2));
    };
    for(const name of modules){
      await page.getByTestId('settings-tab-'+(ids[name]||name.toLowerCase())).click();
      await capture(name);
      const text=translated[name].map(n=>n.text).join('\n');
      for(const phrase of expected[name]) assert.ok(text.includes(phrase), name+': missing '+phrase);
    }
    const dismiss=async()=>{
      await page.keyboard.press('Escape');await page.waitForTimeout(300);
      if(!await page.getByTestId('settings-modal').isVisible()){
        await page.getByRole('button',{name:/^(账号菜单|Account menu)/}).click();
        await page.getByRole('menuitem',{name:/^(设置|Settings)$/}).click();
      }
    };
    await page.getByTestId('settings-tab-permissions').click();
    await page.getByTestId('permissions-section-revoke-local_exec').click();
    await capture('Permissions/revoke-confirmation');
    assert.ok((await page.locator('body').innerText()).includes('撤销全部本地计算授权？'));
    assert.ok((await page.locator('body').innerText()).includes('当前显示的 2 条本地计算允许规则'));
    await dismiss();
    const dialogs = [
      ['Skills','添加技能'], ['Skills','从 GitHub 导入'], ['Connectors','添加连接器'],
      ['Specialists','添加专家'], ['Compute','添加 SSH 主机'], ['Network','配置'],
      ['Network','添加域名'], ['Credentials','添加'],
      ['Memory','新建分类'],
    ];
    for(const [name,label] of dialogs){
      await page.getByTestId('settings-tab-'+(ids[name]||name.toLowerCase())).click();
      await page.getByRole('button',{name:label,exact:true}).click();
      await capture(name+'/'+label);
      if(['添加技能','添加专家'].includes(label)){
        await page.getByRole('menuitem',{name:/^从头编写/}).click();
        await capture(name+'/editor');
      }
      if(label==='添加连接器'){
        const custom=page.getByRole('menuitem',{name:/自定义/});
        if(await custom.count()){await custom.first().click();await capture('Connectors/editor');}
      }
      await dismiss();
    }
    for(const provider of ['AWS','GitHub','Google Cloud','Microsoft Azure','Modal','NVIDIA API','OpenAlex','文献访问（期刊等）']){
      await page.getByTestId('settings-tab-credentials').click();
      // Provider cards have a connect button and a unique visible label.
      const button=page.getByRole('button',{name:'连接',exact:true});
      const index=['AWS','GitHub','Google Cloud','文献访问（期刊等）','Microsoft Azure','Modal','NVIDIA API','OpenAlex'].indexOf(provider);
      await button.nth(index).click();await capture('Credentials/'+provider);
      await dismiss();
    }
    await page.getByTestId('settings-tab-storage').click();
    await page.getByRole('button',{name:'更改位置',exact:true}).click();await capture('Storage/location');
    await dismiss();
    await page.getByTestId('settings-tab-general').click();
    await page.getByRole('button',{name:'设置',exact:true}).click();await capture('General/contact-email');
    await dismiss();
    for(const label of ['默认模型','推理强度','子代理模型','回复字体','主题']){
      // Native dropdowns expose labels through their accessible names.
      const control=page.getByRole('combobox',{name:label,exact:true});
      if(await control.count()) {await control.click();await capture('General/'+label);await dismiss();}
    }
    for(const value of ['Opus 5','与主模型相同','Anthropic Sans','跟随系统']){
      const control=page.getByTestId('settings-modal').getByRole('combobox').filter({hasText:value});
      assert.equal(await control.count(),1,'Dropdown missing: '+value);
      await control.click();await capture('General/options/'+value);await dismiss();
    }
    await page.screenshot({path:root+'/settings-zh.png'});
    writeFileSync(root+'/settings-translated.json', JSON.stringify(translated,null,2));
    await page.evaluate(() => window.__csswitchZh.setEnabled(false));
    writeFileSync(config, '{"science_zh":false}');
    assert.ok((await page.getByTestId('settings-modal').innerText()).includes('Reasoning effort'));
    assert.equal(errors.length,0,errors.join(';'));
    console.log('PASS all 11 native settings modules, dialogs, dropdowns and translation restoration; React errors: 0');
  }
} finally {
  await browser.close();
}
