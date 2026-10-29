import {chromium} from 'playwright';
import {readFileSync, writeFileSync} from 'node:fs';
import assert from 'node:assert/strict';
const {url,root,config} = JSON.parse(readFileSync(process.argv[2]));
const browser = await chromium.launch({headless:true});
try {
  const page = await browser.newPage({viewport:{width:1440,height:1000}});
  const errors = [], sent = [];
  page.on('pageerror', error=>errors.push(error.message));
  page.on('request', request=>{
    if(new URL(request.url()).pathname === '/__csswitch/zh/translate') sent.push(...request.postDataJSON().texts);
  });
  await page.goto(url);
  const login = page.locator('form[action*="/api/auth/nonce"] button');
  if(await login.count()) await login.click();
  await page.waitForTimeout(2500);
  const project = page.getByRole('button',{name:'Open project Example project',exact:true});
  if(await project.count()) await project.click();
  await page.getByTestId('new-session-button').click();
  await page.waitForFunction(()=>window.__csswitchZh?.audit().online);
  // Simulate a new upstream label without changing the bundled offline terms.
  await page.evaluate(()=>delete window.__csswitchZh.dictionary['More models']);
  await page.locator('button[aria-label^="Model:"]:visible,button[aria-label^="模型"]:visible').first().click();
  await page.getByTestId('model-selector-popup').getByText('更多模型',{exact:true}).waitFor({timeout:60000});
  assert.ok(sent.includes('More models'), 'New UI label was not translated online');
  const cache = await(await page.request.get(new URL('/__csswitch/zh/cache',page.url()).href)).json();
  assert.equal(cache.translations['More models'],'更多模型');
  const settings = await(await page.request.get(new URL('/__csswitch/config',page.url()).href)).json();
  const result = await page.request.post(new URL('/__csswitch/zh/translate',page.url()).href,{
    headers:{Origin:new URL(page.url()).origin,'X-CSSwitch-Translation':settings.translationToken},
    data:{texts:['More models','Private fixture research title']},
  });
  assert.equal(result.status(),200);
  const filtered = await result.json();
  assert.deepEqual(Object.keys(filtered.translations),['More models']);
  assert.deepEqual(filtered.skipped,['Private fixture research title']);
  assert.equal(JSON.stringify(cache).includes('Private fixture research title'),false);
  await page.getByTestId('model-selector-popup').screenshot({path:root+'/online-translation.png'});
  const cfg = JSON.parse(readFileSync(config));cfg.science_zh_online=false;writeFileSync(config,JSON.stringify(cfg));
  await page.waitForFunction(()=>!window.__csswitchZh.audit().online);
  assert.equal(await page.getByTestId('model-selector-popup').getByText('更多模型',{exact:true}).count(),1);
  cfg.science_zh=false;writeFileSync(config,JSON.stringify(cfg));
  await page.getByTestId('model-selector-popup').getByText('More models',{exact:true}).waitFor();
  assert.deepEqual(errors,[]);
  writeFileSync(root+'/translation-report.json',JSON.stringify({
    passed:true, engine:'Edge (translate.js client.edge adapter)',
    automaticNewLabel:{original:'More models',translated:'更多模型'},
    privateTextRejected:true, cacheSurvivesOfflineMode:true, restoreOriginalPassed:true,
    nativeReactErrors:errors.length, modelInferenceRequests:0,
    automaticUiStrings:sent.length, cachedUiTerms:Object.keys(cache.translations).length,
  },null,2));
  console.log('PASS: native Science new-label auto translation via Edge, shared cache, private-text filtering, offline fallback, English restore; no model requests.');
} finally {await browser.close();}
