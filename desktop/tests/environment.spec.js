import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';

async function setup(page, warning = false) {
  await page.addInitScript(({ warning }) => {
    const now = Date.now() / 1000;
    window.environmentFixture = {
      enabled: true, checked_at: now, stale: false, interval: 300, min_interval: 60, level: warning ? 'warning' : 'unknown',
      observations: { api: { ok: true, ip: '2001:4860:4860::8888', country: 'US', latency_ms: 123 }, web: { ok: true, ip: '8.8.4.4', country: 'JP', latency_ms: 84 }, general: { ok: false, error: 'network' } },
      intelligence: { ip: '2001:4860:4860::8888', endpoint: 'api', status: 'unknown', source: 'Net.Coffee', trust_score: 99, is_residential: true, country: 'Taiwan', city: 'Chang-hua', organization: 'Chunghwa Telecom Co., Ltd.', asn: 3462 },
      checks: [
        { id: 'api', level: 'ok', title: 'Anthropic API 连通', detail: '公开端点 HTTPS 验证通过；不代表模型请求成功。' },
        { id: 'region', level: 'ok', title: '出口地区在支持名单内', detail: 'US · 官方名单核对于 2026-11-10。' },
        { id: 'routing', level: 'notice', title: '不同域名出口不一致', detail: '可能存在分流或 IPv4 / IPv6 差异。' },
        { id: 'is_tor', level: warning ? 'warning' : 'unknown', title: warning ? 'Tor 出口：有标记' : 'Tor 出口：未知', detail: '第三方信息不能作为封号预测。' },
        { id: 'is_vpn', level: 'unknown', title: 'VPN IP：未知', detail: '<img src=x onerror=window.xss=true>第三方情报暂不可用。' },
        { id: 'is_abuser', level: 'unknown', title: '滥用记录：未知', detail: '缺失结果不能当作无风险。' },
      ],
      history: [{ at: now - 600, endpoint: 'api', from: '1.1.1.1', to: '8.8.8.8', from_country: 'US', to_country: 'US' }],
      region_verified: '2026-11-10',
    };
    window.environmentCalls = []; window.environmentListeners = {};
    window.emitEnvironment = () => window.environmentListeners['environment-updated']?.({ payload: structuredClone(window.environmentFixture) });
    window.__TAURI__ = {
      event: { listen: async (name, handler) => { window.environmentListeners[name] = handler; return () => {}; } },
      core: { invoke: async (cmd, args = {}) => {
        window.environmentCalls.push({ cmd, args });
        if (cmd === 'get_config') return { mode: 'official', provider: '', connections: [], proxy_port: 18992, sandbox_port: 8991 };
        if (cmd === 'app_version') return '0.9.0';
        if (cmd === 'check_update') return { available: false };
        if (cmd === 'read_logs') return { proxy: 'ready', sandbox: '' };
        if (cmd === 'subscription_manage') return { accounts: [], ready_accounts: 0, native: {}, science: {} };
        if (cmd === 'environment_open_source') return;
        if (cmd !== 'environment_manage') throw new Error(cmd);
        if (args.operation === 'refresh' && window.holdEnvironment) await new Promise(resolve => { window.releaseEnvironment = resolve; });
        if (window.failEnvironment) throw new Error('request failed');
        if (args.operation === 'configure') window.environmentFixture.enabled = args.enabled;
        if (args.operation === 'clear') window.environmentFixture = { enabled: window.environmentFixture.enabled, history: [] };
        return structuredClone(window.environmentFixture);
      } },
    };
  }, { warning });
  await page.goto('/');
  await page.locator('[data-view="environment"]').click();
  await expect(page.locator('#environmentPanel')).toBeVisible();
}

test('shows complete IPs by default, supports persistent hiding and escapes untrusted data', async ({ page }) => {
  await setup(page);
  await expect(page.locator('.environment-summary h3')).toHaveText('检测信息不完整');
  await expect(page.locator('#environmentShowIP')).not.toBeChecked();
  await expect(page.locator('.environment-exits')).toContainText('2001:4860:4860::8888');
  await expect(page.locator('.environment-history')).toContainText('1.1.1.1');
  await expect(page.locator('#environmentPanel img:not(.env-flag)')).toHaveCount(0);
  await expect(page.locator('#environmentPanel')).toContainText('不能保证避免封号');
  await page.locator('#environmentShowIP').check();
  await expect(page.locator('.environment-exits')).toContainText('2001:4860:••••');
  await expect(page.locator('#environmentPanel')).not.toContainText('1.1.1.1');
  await page.reload();
  await page.locator('[data-view="environment"]').click();
  await expect(page.locator('#environmentShowIP')).toBeChecked();
  await page.locator('#environmentShowIP').uncheck();
  await expect(page.locator('.environment-exits')).toContainText('2001:4860:4860::8888');
});

test('only manual detection contacts the network, including after navigation and elapsed timers', async ({ page }) => {
  await page.clock.install();
  await setup(page);
  await expect(page.getByRole('switch', { name: '实时环境监测' })).toHaveCount(0);
  await expect(page.locator('.env-toolbar')).toContainText('仅手动检测');
  await page.clock.fastForward(600_000);
  await page.locator('[data-view="logs"]').click();
  await page.locator('[data-view="environment"]').click();
  expect(await page.evaluate(() => window.environmentCalls.filter(c => c.cmd === 'environment_manage' && c.args.operation !== 'status'))).toHaveLength(0);
  await page.locator('#environmentRefresh').click();
  await expect(page.locator('#environmentRefresh')).toBeEnabled();
  const calls = await page.evaluate(() => window.environmentCalls);
  expect(calls.filter(c => c.cmd === 'environment_manage' && c.args.operation !== 'status').map(c => c.args.operation)).toEqual(['refresh']);
  expect(calls.filter(c => c.cmd === 'subscription_manage').every(c => c.args.operation === 'status')).toBe(true);
});

test('immediate detection replaces the exit without a top-right refresh', async ({ page }) => {
  await setup(page);
  for (const ip of ['123.123.123.123', '218.208.123.123']) {
    await page.evaluate(ip => {
      window.environmentFixture.observations.api.ip = ip;
      window.environmentFixture.checked_at = Date.now() / 1000;
    }, ip);
    await page.locator('#environmentRefresh').click();
    await expect(page.locator('.environment-exit').last()).toContainText(ip);
  }
  expect(await page.evaluate(() => window.environmentCalls.filter(c => c.cmd === 'environment_manage' && c.args.operation === 'refresh').length)).toBe(2);
});

test('explicit service cooldown explains why a fresh scan is unavailable', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.environmentFixture.refresh_deferred_until = Date.now() / 1000 + 1800; });
  await page.locator('#environmentRefresh').click();
  await expect(page.locator('.environment-summary h3')).toHaveText('检测服务冷却中');
  await expect(page.getByRole('status')).toContainText('本次未重新联网');
});

test('native alerts update across pages without duplicate scans and stale evidence loses live status', async ({ page }) => {
  await setup(page, true);
  await page.locator('[data-view="logs"]').click();
  await expect(page.locator('#environmentAlert')).toBeVisible();
  await page.evaluate(() => { window.environmentFixture.checked_at -= 400; window.emitEnvironment(); });
  await expect(page.locator('#environmentBadge')).toContainText('结果待更新');
  await expect(page.locator('#environmentAlert')).toBeHidden();
  await expect(page.locator('#logText')).toHaveText('ready');
  const scans = await page.evaluate(() => window.environmentCalls.filter(c => c.cmd === 'environment_manage' && c.args.operation !== 'status'));
  expect(scans).toHaveLength(0);
});

test('slow manual scans allow navigation, deduplicate clicks and do not replace a later page', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.holdEnvironment = true; });
  await page.locator('#environmentRefresh').click();
  await expect(page.locator('#environmentRefresh')).toBeDisabled();
  await page.locator('[data-view="logs"]').click();
  await page.evaluate(() => window.releaseEnvironment());
  await expect(page.locator('#logText')).toHaveText('ready');
  expect(await page.evaluate(() => window.environmentCalls.filter(c => c.cmd === 'environment_manage' && c.args.operation === 'refresh').length)).toBe(1);
});

test('failed scans retain previous observations with an explicit error', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => { window.failEnvironment = true; });
  await page.locator('#environmentRefresh').click();
  await expect(page.locator('.environment-error')).toContainText('上次结果仅供参考');
  await expect(page.locator('.environment-summary h3')).toHaveText('检测不可用');
  await expect(page.locator('.environment-exits')).toContainText('2001:4860:4860::8888');
});

test('clear confirms scope and source links do not open Science', async ({ page }) => {
  await setup(page);
  await page.getByText('监测频率、检测范围与数据来源', {exact:true}).click();
  await page.locator('#environmentRegionSource').click();
  expect(await page.evaluate(() => window.environmentCalls.at(-1))).toEqual({ cmd: 'environment_open_source', args: { source: 'regions' } });
  await page.locator('#environmentClear').click();
  await page.locator('#confirmDialog button[value="confirm"]').click();
  await expect(page.locator('.environment-history')).toContainText('暂无观察记录');
  await expect(page.locator('.environment-summary h3')).toHaveText('等待首次检测');
});

test('desktop and narrow layouts fit with full IPv6 and live updates keep keyboard focus', async ({ page }) => {
  await setup(page, true);
  await page.locator('#environmentShowIP').uncheck();
  await page.locator('#environmentRefresh').focus();
  await page.evaluate(() => window.emitEnvironment());
  await expect(page.locator('#environmentRefresh')).toBeFocused();
  mkdirSync('../artifacts', { recursive: true });
  await page.setViewportSize({ width: 1180, height: 850 });
  await page.screenshot({ path: '../artifacts/environment-desktop.png', fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: '../artifacts/environment-mobile.png', fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(await page.locator('.environment-exits').evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
});

test('DNS inspection is explicit and never marks an empty result as safe', async ({ page }) => {
  await setup(page);
  await expect(page.locator('.env-dns')).toContainText('未检测');
  await page.evaluate(() => { window.environmentFixture.dns = { checked_at: Date.now() / 1000, status: 'unknown', rows: [], reference_ip: '8.8.4.4' }; });
  await page.locator('#environmentDNS').click();
  await expect(page.locator('.env-dns')).toContainText('未取得有效结果');
  expect(await page.evaluate(() => window.environmentCalls.filter(c => c.cmd === 'environment_manage' && c.args.operation === 'dns').length)).toBe(1);
});

test('WebRTC sends only server-reflexive candidates and always closes the connection', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => {
    window.RTCPeerConnection = class {
      createDataChannel() {}
      async createOffer() { return {}; }
      async setLocalDescription() {
        this.onicecandidate({ candidate: { candidate: 'candidate:1 1 udp 1 192.168.1.2 9 typ host', address: '192.168.1.2' } });
        this.onicecandidate({ candidate: { candidate: 'candidate:2 1 udp 1 8.8.4.4 9 typ srflx', address: '8.8.4.4' } });
        this.onicecandidate({ candidate: null });
      }
      close() { window.environmentRTCClosed = true; }
    };
  });
  await page.locator('#environmentUDP').click();
  await expect(page.locator('#environmentUDP')).toBeEnabled();
  const result = await page.evaluate(() => ({ closed: window.environmentRTCClosed, call: window.environmentCalls.find(c => c.cmd === 'environment_manage' && c.args.operation === 'udp') }));
  expect(result.closed).toBe(true);
  expect(result.call.args.ips).toEqual(['8.8.4.4']);
  expect(Object.keys(result.call.args)).toEqual(['operation', 'ips']);
});


test('full IPv4 fits on one line at the normal desktop window size', async ({ page }) => {
  await setup(page);
  await page.evaluate(() => {
    for (const key of ['china', 'general', 'api']) window.environmentFixture.observations[key] = { ok: true, ip: '218.208.123.123', country: 'MY' };
    window.emitEnvironment();
  });
  for (const width of [980, 1180]) {
    await page.setViewportSize({ width, height: 850 });
    for (const address of await page.locator('.environment-ip-value').all()) {
      await expect(address).toHaveText('218.208.123.123');
      const fits = await address.evaluate(el => {
        const style = getComputedStyle(el);
        return el.scrollWidth <= el.clientWidth && el.getBoundingClientRect().height <= parseFloat(style.lineHeight) + 1;
      });
      expect(fits).toBe(true);
    }
  }
});
