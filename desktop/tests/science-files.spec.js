import { test, expect } from '@playwright/test';
import { readFileSync, mkdirSync } from 'node:fs';
const plugin = readFileSync(new URL('../../scripts/science-files.js', import.meta.url), 'utf8');

async function setup(page, enabled = true) {
  const state = { enabled, calls: [] };
  await page.route('**/__csswitch/files/config', route => route.fulfill({json: {enabled: state.enabled, token: state.enabled ? 'fixture-token' : null}}));
  await page.route('**/__csswitch/files/action', async route => {
    state.calls.push({body: route.request().postDataJSON(), headers: route.request().headers()});
    await route.fulfill({ status: state.error ? 404 : 200, json: state.error ? {error: state.error} : {message: '已在 Finder 中显示。'} });
  });
  await page.goto('/');
  await page.setContent('<style>body{background:#f7f6f3;color:#302e2a;font:14px system-ui} [role=menu]{position:fixed;top:560px;left:790px;width:220px;background:white;padding:6px;border:1px solid #ddd;border-radius:12px;box-shadow:0 4px 20px #0001} [role=menu]>button{display:block;width:100%;padding:9px 12px;background:none;border:0;text-align:left;color:inherit;font:inherit}</style><p>Science 文件预览 · report.md</p>');
  await page.addScriptTag({content: plugin});
  return state;
}
async function menu(page, kind = 'artifact', extra = {}) {
  await page.evaluate(({kind, extra}) => {
    document.querySelector('[role=menu]')?.remove();
    const element = document.createElement('div'); element.setAttribute('role', 'menu'); element.setAttribute('aria-label', 'Artifact actions');
    element.innerHTML = '<button role="menuitem">Open in Artifact Viewer</button><button role="menuitem">Copy link</button><button role="menuitem">Download</button>';
    let props = {file: {ref: {kind: 'artifact', artifactId: 'art_1', versionId: 'v1'}}};
    if (kind === 'remote' || kind === 'local') props.file.ref = {kind, absPath:'/tmp/private.md'};
    if (kind === 'multiple') props.selectedCount = 2;
    if (kind === 'tab') props = {tab: {artifactId:'art_1',versionId:'historic'}};
    if (kind === 'resolved') props = {resolvedArtifactId:'art_1',resolvedVersionId:'v1'};
    if (kind === 'preview' || kind === 'uncertain') props = {tile: {artifactId: 'art_1', versionId: 'latest'}};
    const parent = {memoizedProps: props};
    let fiber = {memoizedProps: {}, return: parent};
    if (kind === 'preview') {
      parent.child = {memoizedProps:{artifact:{id:'art_1'},versionId:'historic'}};
      fiber.memoizedProps = {onClose(){}, x:23, y:45};
      // The installed Science viewer has 161 hooks; its menu hook has only a
      // position, so the displayed version must come from the preview subtree.
      for (let i=0;i<161;i++) parent.memoizedState = {memoizedState:i === 160 ? {position:{x:23,y:45}} : null,next:parent.memoizedState};
    }
    if (['library', 'tray', 'inline', 'ambiguous', 'unknown-hook'].includes(kind)) {
      fiber.memoizedProps = {onClose(){}, x:23, y:45};
      const data = kind === 'library' ? {id:'from_menu',version_id:'v3'} : kind === 'tray' ? {artifact_id:'from_menu',version_id:'v3'} : {artifactId:'from_menu',versionId:'v3'};
      if (kind === 'unknown-hook') {delete data.artifactId; delete data.versionId; data.path='/tmp/x';}
      parent.memoizedState = {memoizedState:{position:{x:23,y:45},data}};
      if (kind === 'ambiguous') parent.memoizedState.next = {memoizedState:{position:{x:23,y:45},data:{id:'other',version_id:'v3'}}};
    }
    if (kind === 'alternate') {
      parent.stateNode = {current:{}};
      fiber.alternate = {memoizedProps:{tab:{artifactId:'active',versionId:'v4'}}};
    }
    if (kind !== 'missing') element.__reactFiber$fixture = fiber;
    if (extra.noDownload) element.querySelector('button:last-child').remove();
    if (extra.label) element.setAttribute('aria-label', extra.label);
    document.body.append(element);
  }, {kind, extra});
}

test('file plugin is independent of translation, adds once, and live disable restores menu', async ({page}) => {
  const state = await setup(page, false); await menu(page);
  await expect(page.locator('[data-csswitch-files]')).toHaveCount(0);
  state.enabled = true;
  await expect(page.getByRole('menuitem', {name:'在文件夹中显示'})).toBeVisible({timeout:6000});
  await page.evaluate(() => document.body.append(document.createElement('p')));
  await expect(page.locator('[data-csswitch-files]')).toHaveCount(1);
  state.enabled = false;
  await expect(page.locator('[data-csswitch-files]')).toHaveCount(0, {timeout:6000});
  await expect(page.getByRole('menuitem', {name:'Download', exact:true})).toBeVisible();
  expect(await page.getByRole('menu').evaluate(node=>node.style.top)).toBe('');
  expect(state.calls).toEqual([]);
});

test('each action sends the exact menu identity and synthetic clicks do nothing', async ({page}) => {
  const state = await setup(page); await menu(page);
  const button = page.getByRole('menuitem',{name:'在文件夹中显示'});
  await expect(button).toBeVisible();
  await button.evaluate(node=>node.click()); expect(state.calls).toHaveLength(0);
  for (const [action, label] of [['reveal','在文件夹中显示'],['open','用默认应用打开'],['edit','用文本编辑器打开']]) {
    await page.getByRole('menuitem',{name:label}).click();
    await expect.poll(()=>state.calls.length).toBe(['reveal','open','edit'].indexOf(action)+1);
    expect(state.calls.at(-1).body).toEqual({artifactId:'art_1',versionId:'v1',action});
    expect(state.calls.at(-1).headers['x-csswitch-files']).toBe('fixture-token');
    await expect(button).toBeEnabled();
  }
});

test('library, generated files and inline references prefer clicked menu over enclosing preview', async ({page}) => {
  const state = await setup(page);
  for (const kind of ['library','tray','inline']) {
    await menu(page,kind); await page.getByRole('menuitem',{name:'在文件夹中显示'}).click();
    await expect.poll(()=>state.calls.length).toBe(['library','tray','inline'].indexOf(kind)+1);
    expect(state.calls.at(-1).body).toEqual({artifactId:'from_menu',versionId:'v3',action:'reveal'});
    await expect(page.getByRole('menuitem',{name:'在文件夹中显示'})).toBeEnabled();
  }
});

test('preview uses displayed historical version, active React tree and image wrappers', async ({page}) => {
  const state = await setup(page);
  for (const [kind, id, version] of [['preview','art_1','historic'],['tab','art_1','historic'],['alternate','active','v4'],['resolved','art_1','v1']]) {
    const before = state.calls.length;
    await menu(page, kind, {noDownload:true}); await page.getByRole('menuitem',{name:'用默认应用打开'}).click();
    await expect.poll(()=>state.calls.length).toBe(before+1);
    expect(state.calls.at(-1).body).toEqual({artifactId:id,versionId:version,action:'open'});
    await expect(page.getByRole('menuitem',{name:'用默认应用打开'})).toBeEnabled();
  }
});

test('multiple, ambiguous, unknown, remote and unverified local targets never fall back or download', async ({page}) => {
  const state = await setup(page); let downloads = 0; page.on('download',()=>downloads++);
  for (const kind of ['multiple','ambiguous','unknown-hook','remote','local','missing','uncertain']) {
    await menu(page,kind); await page.getByRole('menuitem',{name:'在文件夹中显示'}).click();
    await expect(page.getByRole('alert')).toBeVisible();
    expect(state.calls).toHaveLength(0);
  }
  expect(downloads).toBe(0);
});

test('missing local files report inline errors without replacing native download', async ({page}) => {
  const state = await setup(page); state.error = '本地文件已移动或删除，无法打开。'; await menu(page);
  await page.getByRole('menuitem',{name:'用默认应用打开'}).click();
  await expect(page.getByRole('alert')).toContainText(state.error);
  await expect(page.getByRole('menuitem',{name:'Download',exact:true})).toBeVisible();
  expect(state.calls).toHaveLength(1);
});

test('translated dynamic menus fit small windows and support keyboard operation', async ({page}) => {
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const state=await setup(page); await page.setViewportSize({width:390,height:560}); await menu(page,'artifact',{label:'文件操作'});
  const reveal=page.getByRole('menuitem',{name:'在文件夹中显示'}); await expect(reveal).toBeVisible();
  const box=await page.getByRole('menu').boundingBox();
  expect(box.x+box.width).toBeLessThanOrEqual(390); expect(box.y+box.height).toBeLessThanOrEqual(560);
  await reveal.focus(); await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem',{name:'用默认应用打开'})).toBeFocused();
  await page.keyboard.press('Enter'); await expect.poll(()=>state.calls.length).toBe(1);
  await page.getByRole('button',{name:'关闭文件操作提示'}).click();
  mkdirSync(new URL('../../artifacts/',import.meta.url),{recursive:true});
  await page.screenshot({path:new URL('../../artifacts/science-files-mobile.png',import.meta.url).pathname});
  await page.setViewportSize({width:980,height:720}); await menu(page);
  await expect(page.getByRole('menuitem',{name:'在文件夹中显示'})).toBeVisible();
  await page.screenshot({path:new URL('../../artifacts/science-files-desktop.png',import.meta.url).pathname});
  expect(errors).toEqual([]);
});
