const usageState = { tab: 'overview', range: 'all', provider: 'all', model: 'all', page: 0, pricing: {} };
const countToken = value => Math.max(0, Number(value) || 0);
const usd = value => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 4 }).format(value || 0);
const usageProvider = id => state.config.connections.find(p => p.id === id)?.name || id || '未知连接';
async function loadUsage() {
  const [journal, pricing] = await Promise.allSettled([call('read_usage'), workspaceCall('pricing_get')]);
  if (journal.status === 'rejected') throw journal.reason;
  state.usage = journal.value.entries || [];
  usageState.pricing = pricing.status === 'fulfilled' ? pricing.value?.pricing || {} : usageState.pricing;
  if (pricing.status === 'rejected') notice('模型定价读取失败：' + String(pricing.reason.message || pricing.reason), true);
}
function usageEntries() {
  const now = new Date(), start = new Date(now); start.setHours(0, 0, 0, 0);
  if (usageState.range !== 'all') start.setDate(start.getDate() - (Number(usageState.range) - 1));
  return state.usage.filter(e => (usageState.range === 'all' || e.time * 1000 >= start.getTime()) && (usageState.provider === 'all' || e.provider === usageState.provider) && (usageState.model === 'all' || e.model === usageState.model));
}
function usageCost(e) {
  const rate = Object.hasOwn(usageState.pricing, e.model) ? usageState.pricing[e.model] : null; if (!rate) return null;
  const cached = countToken(e.cache_read_input_tokens), write = countToken(e.cache_creation_input_tokens);
  const protocol = e.protocol || state.config.connections.find(p => p.id === e.provider)?.protocol;
  const input = Math.max(0, countToken(e.input_tokens) - (['openai', 'responses'].includes(protocol) ? cached : 0));
  return (input * rate.input + countToken(e.output_tokens) * rate.output + cached * rate.cache_read + write * rate.cache_write) / 1e6;
}
function renderUsage() {
  const providers = [...new Set(state.usage.map(e => e.provider))], models = [...new Set(state.usage.map(e => e.model))];
  const tabs = [['overview', '总览'], ['requests', '请求记录'], ['pricing', '模型定价']];
  $('#content').innerHTML = `<div class="page-intro"><div><h2>调用统计 ${help('统计口径', '统计 CSSwitch 网关的最近 2000 条请求，不保存提示词和密钥。费用按模型定价估算，未配置价格的请求不计入费用。')}</h2></div><button class="btn" id="exportUsageBtn" title="导出用量 CSV" aria-label="导出用量 CSV">${icon('download')}导出 CSV</button></div><div class="management-toolbar"><div class="segmented" role="group" aria-label="用量视图">${tabs.map(([id, label]) => `<button data-usage-tab="${id}" class="${usageState.tab === id ? 'active' : ''}">${label}</button>`).join('')}</div></div><div class="usage-filters"><select id="usageRange" aria-label="用量时间范围">${[['all', '全部记录'], ['1', '今天'], ['7', '最近 7 天'], ['30', '最近 30 天']].map(([id, label]) => `<option value="${id}" ${usageState.range === id ? 'selected' : ''}>${label}</option>`).join('')}</select><select id="usageProvider" aria-label="用量提供商"><option value="all">全部提供商</option>${providers.map(id => `<option value="${esc(id)}" ${usageState.provider === id ? 'selected' : ''}>${esc(usageProvider(id))}</option>`).join('')}</select><select id="usageModel" aria-label="用量模型"><option value="all">全部模型</option>${models.map(id => `<option value="${esc(id)}" ${usageState.model === id ? 'selected' : ''}>${esc(id)}</option>`).join('')}</select></div><div id="usageBody"></div>`;
  document.querySelectorAll('[data-usage-tab]').forEach(b => b.onclick = () => { usageState.tab = b.dataset.usageTab; usageState.page = 0; renderUsage(); });
  for (const [id, key] of [['usageRange', 'range'], ['usageProvider', 'provider'], ['usageModel', 'model']]) $( '#' + id).onchange = e => { usageState[key] = e.target.value; usageState.page = 0; renderUsageBody(); };
  $('#exportUsageBtn').onclick = () => {
    const entries = usageEntries(), columns = ['time', 'provider', 'model', 'input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens', 'latency_ms', 'ok', 'estimated_cost_usd'];
    const csv = [columns.join(','), ...entries.map(e => columns.map(c => `"${String(c === 'estimated_cost_usd' ? usageCost(e) ?? '' : e[c] ?? '').replace(/^[=+@-]/, "'$&").replace(/"/g, '""')}"`).join(','))].join('\r\n');
    download('csswitch-usage.csv', '\uFEFF' + csv, 'text/csv');
  }; renderUsageBody(); icons();
}
function renderUsageBody() {
  const entries = usageEntries(), sum = key => entries.reduce((n, e) => n + countToken(e[key]), 0);
  const costs = entries.map(usageCost), priced = costs.filter(c => c !== null), failures = entries.filter(e => !e.ok).length;
  $('#exportUsageBtn').disabled = !entries.length;
  if (usageState.tab === 'pricing') { renderUsagePricing(); return; }
  const cards = [['记录请求', fmt(entries.length), `${failures} 次失败`], ['输入 Tokens', fmt(sum('input_tokens')), `缓存读取 ${fmt(sum('cache_read_input_tokens'))}`], ['输出 Tokens', fmt(sum('output_tokens')), `平均耗时 ${fmt(entries.length ? Math.round(sum('latency_ms') / entries.length) : 0)} ms`], ['预估费用', priced.length ? usd(priced.reduce((a, b) => a + b, 0)) : '未设置', priced.length < entries.length ? `${entries.length - priced.length} 条请求未设置定价` : 'USD · 自定义模型定价']];
  $('#usageBody').innerHTML = `<div class="metrics">${cards.map(([label, value, hint]) => `<div class="metric"><div class="caption">${label}</div><div class="number">${value}</div><div class="subtle">${hint}</div></div>`).join('')}</div>${usageState.tab === 'overview' ? `<section class="chart-panel"><div class="section-heading"><h2>Token 用量趋势</h2><div class="chart-legend"><span><i class="legend-dot input"></i>输入</span><span><i class="legend-dot output"></i>输出</span></div></div><div id="usageChart"></div></section><div class="section-heading"><h2>模型统计</h2><span class="subtle">按当前筛选汇总</span></div>${usageGroups(entries)}` : ''}<div class="section-heading"><h2>${usageState.tab === 'overview' ? '最近请求' : '请求记录'}</h2><span class="subtle">${fmt(entries.length)} 条 · 成功率 ${entries.length ? ((entries.length - failures) / entries.length * 100).toFixed(1) : '0'}%</span></div><div id="requestTable"></div>`;
  if (usageState.tab === 'overview') renderUsageChart(entries);
  renderRequestTable(entries); icons();
}
function usageGroups(entries) {
  const groups = new Map();
  for (const e of entries) {
    const g = groups.get(e.model) || { name: e.model, requests: 0, input: 0, output: 0, cache: 0, cost: 0, missing: 0 };
    g.requests++; g.input += countToken(e.input_tokens); g.output += countToken(e.output_tokens); g.cache += countToken(e.cache_read_input_tokens);
    const cost = usageCost(e); if (cost === null) g.missing++; else g.cost += cost; groups.set(e.model, g);
  }
  return groups.size ? `<div class="table-wrap"><table><thead><tr><th>模型</th><th>请求</th><th>输入</th><th>输出</th><th>缓存读取</th><th>预估费用</th></tr></thead><tbody>${[...groups.values()].sort((a, b) => b.requests - a.requests).map(g => `<tr><td>${esc(g.name)}</td><td class="numeric">${fmt(g.requests)}</td><td class="numeric">${fmt(g.input)}</td><td class="numeric">${fmt(g.output)}</td><td class="numeric">${fmt(g.cache)}</td><td class="numeric">${g.missing === g.requests ? '未设置' : usd(g.cost)}${g.missing && g.missing !== g.requests ? '<div class="subtle">部分请求未定价</div>' : ''}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">暂无模型用量</div>';
}
function renderUsageChart(entries) {
  if (!entries.length) { $('#usageChart').innerHTML = '<div class="empty">暂无请求记录</div>'; return; }
  const end = usageState.range === 'all' ? new Date(Math.max(...entries.map(e => Number(e.time) || 0)) * 1000) : new Date(); end.setHours(0, 0, 0, 0);
  const days = Number(usageState.range) === 1 ? 1 : Number(usageState.range) === 7 ? 7 : 14;
  const buckets = Array.from({ length: days }, (_, i) => { const date = new Date(end); date.setDate(date.getDate() - days + 1 + i); return { date, input: 0, output: 0 }; });
  for (const e of entries) { const d = new Date(e.time * 1000).toDateString(), b = buckets.find(b => b.date.toDateString() === d); if (b) { b.input += countToken(e.input_tokens); b.output += countToken(e.output_tokens); } }
  const max = Math.max(1, ...buckets.map(b => b.input + b.output));
  $('#usageChart').innerHTML = `<div class="bar-chart" role="img" aria-label="${days} 天输入和输出 Token 趋势">${buckets.map(b => `<div class="chart-column"><div class="bar-track" title="${b.date.toLocaleDateString('zh-CN')}：输入 ${fmt(b.input)}，输出 ${fmt(b.output)}"><div class="bar output" style="height:${b.output / max * 100}%"></div><div class="bar input" style="height:${b.input / max * 100}%"></div></div><span>${b.date.getMonth() + 1}/${b.date.getDate()}</span></div>`).join('')}</div><div class="chart-caption subtle">${buckets[0].date.toLocaleDateString('zh-CN')} — ${end.toLocaleDateString('zh-CN')} · 悬停查看每日用量</div>`;
}
function renderRequestTable(entries) {
  const limit = usageState.tab === 'overview' ? 10 : 25, pages = Math.max(1, Math.ceil(entries.length / limit));
  usageState.page = Math.min(usageState.page, pages - 1);
  $('#requestTable').innerHTML = entries.length ? `<div class="table-wrap"><table><thead><tr><th>时间</th><th>连接 / 模型</th><th>输入</th><th>输出</th><th>缓存</th><th>耗时</th><th>预估费用</th><th>状态</th></tr></thead><tbody>${entries.slice(usageState.page * limit, (usageState.page + 1) * limit).map(e => `<tr><td>${esc(new Date(e.time * 1000).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }))}</td><td>${esc(usageProvider(e.provider))}<div class="connection-model">${esc(e.model)}</div></td><td class="numeric">${fmt(e.input_tokens)}</td><td class="numeric">${fmt(e.output_tokens)}</td><td class="numeric">${fmt(e.cache_read_input_tokens)}</td><td class="numeric">${fmt(e.latency_ms)} ms</td><td class="numeric">${usageCost(e) === null ? '—' : usd(usageCost(e))}</td><td><span class="dot ${e.ok ? 'green' : 'red'}"></span> ${e.ok ? '成功' : '失败'}</td></tr>`).join('')}</tbody></table></div><div class="pagination"><span>第 ${usageState.page + 1} / ${pages} 页</span><div class="actions"><button class="btn" id="usagePrev" ${usageState.page === 0 ? 'disabled' : ''}>上一页</button><button class="btn" id="usageNext" ${usageState.page + 1 >= pages ? 'disabled' : ''}>下一页</button></div></div>` : `<div class="empty">${icon('chart-no-axes-combined')}暂无请求记录</div>`;
  $('#usagePrev')?.addEventListener('click', () => { usageState.page--; renderRequestTable(entries); }); $('#usageNext')?.addEventListener('click', () => { usageState.page++; renderRequestTable(entries); });
}
function renderUsagePricing() {
  const models = [...new Set([...state.usage.map(e => e.model), ...Object.keys(usageState.pricing)])].sort();
  $('#usageBody').innerHTML = `<div class="section-heading"><h2>模型定价</h2><button class="btn primary" id="addPricing" ${!native() ? 'disabled' : ''}>${icon('plus')}添加模型定价</button></div><div class="mcp-note">单位：USD / 百万 Tokens。历史费用随定价调整重新估算，官方模式和未经过网关的请求不计入统计。未定价请求不计入费用。</div>${models.length ? `<div class="table-wrap"><table><thead><tr><th>模型</th><th>输入</th><th>输出</th><th>缓存读取</th><th>缓存写入</th><th></th></tr></thead><tbody>${models.map((model, i) => { const r = usageState.pricing[model]; return `<tr><td>${esc(model)}</td>${['input', 'output', 'cache_read', 'cache_write'].map(k => `<td class="numeric">${r ? usd(r[k]) : '未设置'}</td>`).join('')}<td><button class="btn" data-price-index="${i}">编辑</button></td></tr>`; }).join('')}</tbody></table></div>` : '<div class="empty">添加模型定价后即可估算调用费用。</div>'}`;
  $('#addPricing').onclick = () => openPricing(); document.querySelectorAll('[data-price-index]').forEach(b => b.onclick = () => openPricing(models[Number(b.dataset.priceIndex)])); icons();
}
function openPricing(model = '') {
  const dialog = $('#workspaceDialog'), rate = usageState.pricing[model] || {};
  dialog.innerHTML = `<form id="pricingForm"><div class="dialog-heading"><h2 id="workspaceTitle">模型定价</h2></div><div class="reader-body"><div class="form-grid"><div class="field wide"><label for="pricingModel">模型 ID</label><input id="pricingModel" value="${esc(model)}" maxlength="200" required /></div>${[['input', '输入'], ['output', '输出'], ['cache_read', '缓存读取'], ['cache_write', '缓存写入']].map(([k, label]) => `<div class="field"><label for="price-${k}">${label}（USD / 百万 Tokens）</label><input id="price-${k}" name="${k}" type="number" min="0" max="100000" step="any" value="${rate[k] ?? 0}" required /></div>`).join('')}</div><p class="err" id="pricingError" role="alert" hidden></p></div><div class="dialog-actions"><button class="btn" type="button" id="cancelPricing">取消</button><button class="btn primary">保存定价</button></div></form>`;
  $('#cancelPricing').onclick = () => dialog.close(); $('#pricingForm').onsubmit = e => { e.preventDefault(); action(async () => {
    e.target.inert = true;
    try {
      const model = $('#pricingModel').value.trim(), rates = Object.fromEntries([...new FormData(e.target)].map(([k, v]) => [k, Number(v)]));
      await workspaceCall('pricing_save', { model, rates }); Object.defineProperty(usageState.pricing, model, { value: rates, enumerable: true, writable: true, configurable: true }); dialog.close(); renderUsage(); notice('模型定价已保存。');
    } catch (err) { $('#pricingError').hidden = false; $('#pricingError').textContent = String(err.message || err); }
    finally { e.target.inert = false; }
  }); }; dialog.showModal(); icons();
}
