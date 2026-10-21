// Optional installed-Science integration; launched only by probe-science-ui.py --files.
import { chromium } from 'playwright';
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import assert from 'node:assert/strict';
const {url,config,root}=JSON.parse(readFileSync(process.argv[2],'utf8'));
const browser=await chromium.launch({headless:true});
let page;
try {
  page=await browser.newPage({viewport:{width:1280,height:850}});
  const actions=[], errors=[];
  const failedRequests=[];
  page.on('requestfailed',request=>{
    failedRequests.push({path:new URL(request.url()).pathname,error:request.failure()?.errorText});
    writeFileSync(root+'/files-request-failures.json',JSON.stringify(failedRequests,null,2));
  });
  page.on('pageerror',error=>errors.push(error.message));
  // Resolve real menu identities but never launch applications from sample artifacts.
  await page.route('**/__csswitch/files/action',async route=>{
    actions.push(route.request().postDataJSON()); await route.fulfill({json:{message:'已验证当前文件记录。'}});
  });
  await page.goto(url);
  if(await page.locator('form[action*="/api/auth/nonce"] button').count()) await page.locator('form[action*="/api/auth/nonce"] button').click();
  await page.waitForTimeout(2000);
  const project=page.getByRole('button',{name:'Open project Example project',exact:true});
  const card=page.getByRole('button',{name:/^[\w-]+\.png$/}).first();
  const startupError=page.getByText('Something went wrong',{exact:true});
  await project.or(card).or(startupError).first().waitFor({timeout:45000});
  if(await startupError.isVisible()) {
    // The freshly seeded native demo occasionally fails a lazy module fetch.
    // Retry the fixture page once; never restart or modify the user's instance.
    await page.reload(); errors.length=0;
  }
  await project.or(card).first().waitFor({timeout:45000});
  if(await project.isVisible()) await project.click();
  await page.waitForTimeout(4000);
  assert.ok(await page.evaluate(()=>window.__csswitchFilesLoaded),'Local file plugin missing');
  writeFileSync(root+'/files-ui.txt', await page.locator('body').innerText());
  writeFileSync(root+'/files-buttons.json',JSON.stringify(await page.locator('button,input').evaluateAll(nodes=>nodes.map(n=>({text:n.textContent,label:n.getAttribute('aria-label'),title:n.getAttribute('title'),type:n.getAttribute('type')}))),null,2));
  await page.screenshot({path:root+'/files-before.png'});
  await card.click({button:'right',timeout:15000});
  await page.getByRole('menu').getByText('来源追溯').waitFor({timeout:6000});
  const reveal=page.getByRole('menuitem',{name:'在文件夹中显示',exact:true});
  await reveal.waitFor(); await reveal.click();
  await page.waitForTimeout(300);
  assert.equal(actions.length,1,'Native generated-file menu did not resolve');
  assert.ok(actions[0].artifactId && actions[0].versionId,'Native file version missing');
  const metadata=await page.evaluate(async id=>{
    const response=await fetch('/api/artifacts/'+encodeURIComponent(id)+'/metadata',{credentials:'same-origin'});
    if(!response.ok)throw new Error('Native metadata HTTP '+response.status);
    return response.json();
  },actions[0].artifactId);
  assert.equal(metadata.version_id,actions[0].versionId);
  assert.ok(typeof metadata.file_path==='string','Native resolved path missing');
  const storageExists=existsSync(metadata.file_path);
  await page.screenshot({path:root+'/native-files-menu.png'});
  await page.keyboard.press('Escape');
  await page.getByRole('button',{name:/^(More actions|更多操作)$/}).last().click({timeout:10000});
  await reveal.waitFor(); await reveal.click(); await page.waitForTimeout(300);
  assert.equal(actions.length,2,'Native preview menu did not resolve');
  assert.equal(actions[1].artifactId,actions[0].artifactId);
  assert.equal(actions[1].versionId,actions[0].versionId);
  writeFileSync(config,JSON.stringify({science_zh:true,science_local_files:false}));
  await reveal.waitFor({state:'detached',timeout:6000});
  assert.ok(errors.length===0,errors.join(';'));
  writeFileSync(root+'/files-report.json',JSON.stringify({nativeMenus:2,metadataMatched:true,storageExists,externalAppsLaunched:false,downloads:0,reactErrors:errors.length},null,2));
  console.log('PASS native generated-file and preview menus, exact metadata version, independent toggle; local storage exists: '+storageExists);
}catch(error){
  if(page){writeFileSync(root+'/files-failure.txt',await page.locator('body').innerText());await page.screenshot({path:root+'/files-failure.png'});}
  throw error;
}finally{await browser.close();}
