const root = document.getElementById('viewRoot');
const pageTitle = document.getElementById('pageTitle');
const viewName = document.getElementById('viewName');
const refreshButton = document.getElementById('refreshButton');
const navList = document.getElementById('navList');

const views = {
  overview: { label: '概览', title: '网关运行概览' },
  external: { label: '外部网关', title: '外部 API Gateway 接入预留' },
  services: { label: '服务注册', title: '服务注册与健康状态' },
  projects: { label: '项目与身份', title: '项目身份与 Token 配额' },
  traces: { label: '调用链', title: '调用链与请求观测' },
  usage: { label: '用量与成本', title: 'LLM 调用明细与用量' },
  models: { label: '模型服务', title: 'LLM Service 与逻辑模型' },
  reliability: { label: '可靠性治理', title: 'Endpoint 可靠性与回退' },
  verification: { label: '流式验证', title: '流式治理验证与事件回放' },
  capabilities: { label: '能力矩阵', title: '平台能力矩阵' },
  apps: { label: '业务应用', title: '业务应用接入' },
};

const requestedStreamRun = new URLSearchParams(window.location.search).get('stream_run');
let currentView = requestedStreamRun ? 'verification' : 'overview';
let renderSequence = 0;

function esc(value) {
  const node = document.createElement('div');
  node.textContent = String(value ?? '');
  return node.innerHTML;
}

function formatNumber(value) {
  return new Intl.NumberFormat('zh-CN').format(Number(value || 0));
}

function duration(ms) {
  const value = Number(ms || 0);
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`;
}

function cost(value) {
  return Number(value || 0).toFixed(6);
}

function timeText(iso) {
  if (!iso) return '-';
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  }).format(new Date(iso));
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || '读取数据失败');
  return data;
}

function statusTag(status) {
  const labels = {
    ok: '健康', error: '异常', completed: '完成', failed: '失败', reserved: '预留', connected: '已接入',
    implemented: '已实现', partial: '部分实现', planned: '规划中', enabled: '已启用', disabled: '已停用',
    closed: '正常', open: '已熔断', half_open: '恢复探测', unknown: '未获取',
  };
  return `<span class="status ${esc(status)}">${labels[status] || esc(status)}</span>`;
}

function section(title, body, meta = '') {
  return `<section class="section"><div class="section-heading"><h2>${esc(title)}</h2>${meta ? `<p>${esc(meta)}</p>` : ''}</div>${body}</section>`;
}

function loading() {
  root.innerHTML = '<div class="panel"><div class="empty">正在读取网关真实数据...</div></div>';
}

function failure(message) {
  root.innerHTML = `<div class="panel error"><div class="panel-body">${esc(message)}</div></div>`;
}

async function renderOverview() {
  const [data, capabilities] = await Promise.all([api('/api/overview'), api('/api/capabilities')]);
  const implemented = capabilities.capabilities.filter(item => item.status === 'implemented').length;
  root.innerHTML = `
    <div class="metrics">
      <article class="metric"><div class="metric-label">健康 MCP 服务</div><div class="metric-value">${data.services.healthy} / ${data.services.total}</div><div class="metric-meta">实时健康检查</div></article>
      <article class="metric"><div class="metric-label">已配置项目</div><div class="metric-value">${data.projects.total}</div><div class="metric-meta">API Key 身份识别</div></article>
      <article class="metric"><div class="metric-label">今日 Token 用量</div><div class="metric-value">${formatNumber(data.projects.used_tokens)}</div><div class="metric-meta">日总配额 ${formatNumber(data.projects.daily_limit)}</div></article>
      <article class="metric"><div class="metric-label">近期完成调用链</div><div class="metric-value">${data.traces.completed_traces}</div><div class="metric-meta">失败 ${data.traces.failed_traces}，累计网关耗时均值 ${duration(data.traces.average_trace_duration_ms)}</div></article>
    </div>
    <div class="split-grid section">
      <div class="panel"><div class="panel-body"><div class="section-heading"><h2>内部 MCP 服务网关</h2>${statusTag(data.gateway.status)}</div><div class="service-list"><div class="service-row"><div><div class="service-name">${esc(data.gateway.name)}</div><div class="subtle">${esc(data.gateway.implementation)} · 端口 ${data.gateway.port}</div></div><div class="subtle">服务事件 ${data.traces.stored_events} / ${data.traces.buffer_max_events}</div></div><div class="service-row"><div><div class="service-name">能力状态</div><div class="subtle">${implemented} 项已实现能力，其他能力在矩阵页明确标记。</div></div><button class="link-button" data-view-link="capabilities" type="button">查看矩阵</button></div></div></div></div>
      <div class="panel"><div class="panel-body"><div class="section-heading"><h2>事件保留范围</h2></div><div class="subtle">${esc(data.event_buffer_notice)}</div><div class="metric-value">${data.traces.recent_traces}</div><div class="metric-meta">可查询的近期调用链</div></div></div>
    </div>
    ${section('业务应用', `<div class="app-grid"><article class="app-card"><h3>ODM 测试报告与问题复盘助手</h3><p>作为内部 MCP 服务网关的真实业务消费案例，可在调用后回到服务台查看链路。</p><footer>${statusTag('implemented')}<a class="open-app" href="http://127.0.0.1:8502" target="_blank" rel="noopener">打开应用</a></footer></article></div>`)}
  `;
}

async function renderServices() {
  const data = await api('/api/services');
  const rows = data.services.map(item => `<tr><td><div class="primary-text">${esc(item.label)}</div><div class="subtle">${esc(item.description)}</div></td><td>${statusTag(item.status)}</td><td class="mono">${esc(item.url)}</td><td>${esc(item.protocol)}</td><td>${item.tools ?? '-'} </td></tr>`).join('');
  root.innerHTML = section('已注册 MCP 服务', `<div class="panel"><div class="panel-body"><div class="section-heading"><p>配置来自内部 MCP 服务网关，状态来自实时健康检查。</p><button class="refresh-button" data-health-check type="button">检查健康状态</button></div><div class="table-wrap"><table><thead><tr><th>服务</th><th>状态</th><th>地址</th><th>协议</th><th>工具数</th></tr></thead><tbody>${rows}</tbody></table></div></div></div>`);
}

async function renderExternal() {
  const data = await api('/api/external-gateway');
  root.innerHTML = `
    <div class="metrics">
      <article class="metric"><div class="metric-label">目标网关</div><div class="metric-value">${esc(data.implementation)}</div><div class="metric-meta">外部 API Gateway</div></article>
      <article class="metric"><div class="metric-label">接入状态</div><div class="metric-value">${data.enabled ? '已接入' : '预留'}</div><div class="metric-meta">当前不伪造运行健康度</div></article>
      <article class="metric"><div class="metric-label">当前 HTTP 入口</div><div class="metric-value">8500 / 8502</div><div class="metric-meta">FastAPI 服务直连</div></article>
      <article class="metric"><div class="metric-label">接入后追踪起点</div><div class="metric-value">trace_id</div><div class="metric-meta">由 APISIX 创建或透传</div></article>
    </div>
    ${section('外部 API Gateway（APISIX）', `<div class="panel"><div class="panel-body"><div class="section-heading"><h2>${esc(data.name)}</h2>${statusTag(data.status)}</div><div class="primary-text">${esc(data.current_ingress)}</div><p class="notice">${esc(data.description)}</p></div></div>`)}
    ${section('职责边界', `<div class="panel"><div class="panel-body"><div class="boundary-group"><div class="group-label">企业级 AI 网关实践</div><div class="service-list"><div class="service-row"><div><div class="service-name">APISIX / AGW：低时延数据面</div><div class="subtle">负责高性能请求执行、外部认证、限流、路由、熔断、入口观测与端到端 trace_id。</div></div><div class="subtle">外部 API Gateway</div></div><div class="service-row"><div><div class="service-name">控制面：资源与治理</div><div class="subtle">管理模型、Endpoint、订阅、权限、Key、路由、价格、配额和预算。</div></div><div class="subtle">管理服务</div></div><div class="service-row"><div><div class="service-name">Redis / 配置中心：运行时配置</div><div class="subtle">承载在线权限、模型、场景、路由和 Endpoint 等低时延读取的运行时配置。</div></div><div class="subtle">配置发布</div></div><div class="service-row"><div><div class="service-name">Kafka -> Flink -> Hive / 数仓：运营面</div><div class="subtle">承接调用日志回流、统计计费、异常分析与运营看板。</div></div><div class="subtle">异步数据链路</div></div></div></div><div class="boundary-group current-demo"><div class="group-label">当前本地 Demo</div><div class="service-list"><div class="service-row"><div><div class="service-name">内部 MCP 服务网关</div><div class="subtle">Agent 到 MCP 服务的适配、项目身份、内部调用链和项目配额。</div></div><div class="subtle">当前 8000</div></div><div class="service-row"><div><div class="service-name">LLM Service</div><div class="subtle">模型协议适配、模型调用、逻辑模型选择与 Token 用量返回；当前 Demo 从 SQLite 读取路由，真实场景中由 Redis / 配置中心读取运行时配置。</div></div><div class="subtle">当前 9001</div></div></div></div></div></div>`)}
  `;
}

async function renderProjects() {
  const data = await api('/api/projects');
  const disabled = data.management_enabled ? '' : 'disabled';
  const rows = data.projects.map(item => `<tr data-project-id="${esc(item.project_id)}"><td class="primary-text">${esc(item.project_id)}</td><td class="mono">${esc(item.api_key_hint)}</td><td><select class="control-input" data-project-status ${disabled}><option value="enabled" ${item.status === 'enabled' ? 'selected' : ''}>已启用</option><option value="disabled" ${item.status === 'disabled' ? 'selected' : ''}>已停用</option></select></td><td>${formatNumber(item.used_tokens)}</td><td><input class="control-input number-input" data-project-limit type="number" min="0" value="${item.daily_limit}" ${disabled}></td><td>${item.remaining < 0 ? '不限额' : formatNumber(item.remaining)}</td><td><button class="action-button" data-save-project type="button" ${disabled}>保存</button></td></tr>`).join('');
  const subscriptions = data.subscriptions.length ? data.subscriptions.map(item => `<tr data-subscription-project="${esc(item.project_id)}" data-subscription-scene="${esc(item.logical_name)}"><td class="primary-text">${esc(item.project_id)}</td><td>${esc(item.display_name)}</td><td class="mono">${esc(item.logical_name)}</td><td><select class="control-input" data-subscription-status ${disabled}><option value="enabled" ${item.status === 'enabled' ? 'selected' : ''}>已订阅</option><option value="disabled" ${item.status === 'disabled' ? 'selected' : ''}>已停用</option></select></td><td><button class="action-button" data-save-subscription type="button" ${disabled}>保存</button></td></tr>`).join('') : '<tr><td colspan="5"><div class="empty">暂无项目订阅关系。</div></td></tr>';
  const mode = data.management_enabled ? '管理员配置已加载，保存后下一次调用即时生效。' : '未配置本地管理员密钥，当前为只读展示。';
  root.innerHTML = `${section('项目身份与配额', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>项目</th><th>API Key 标识</th><th>状态</th><th>今日 Token</th><th>日配额</th><th>剩余</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div></div>`, mode)}${section('模型 / 场景订阅', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>项目</th><th>能力</th><th>逻辑场景</th><th>订阅状态</th><th>操作</th></tr></thead><tbody>${subscriptions}</tbody></table></div></div>`, '订阅校验发生在内部 MCP 服务网关调用上游之前')}`;
}

async function renderTraces() {
  const data = await api('/api/traces');
  const rows = data.traces.length ? data.traces.map(item => `<tr><td><button class="link-button mono" data-trace-id="${esc(item.chain_id)}" type="button">${esc(item.chain_id)}</button></td><td>${esc(item.project_id)}</td><td>${statusTag(item.status)}</td><td>${item.service_calls}</td><td>${duration(item.duration_ms)}</td><td>${timeText(item.last_event_at)}</td></tr>`).join('') : '<tr><td colspan="6"><div class="empty">暂无调用链。请先从业务应用完成一次报告生成。</div></td></tr>';
  root.innerHTML = section('近期调用链', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>Chain ID</th><th>项目</th><th>状态</th><th>服务调用</th><th>累计网关耗时</th><th>最近事件</th></tr></thead><tbody>${rows}</tbody></table></div></div><p class="notice">${esc(data.notice)}</p><div id="traceDetail" class="section"></div>`);
}

async function showTrace(chainId) {
  const target = document.getElementById('traceDetail');
  if (!target) return;
  target.innerHTML = '<div class="panel"><div class="empty">正在读取调用链详情...</div></div>';
  try {
    const data = await api(`/api/traces/${encodeURIComponent(chainId)}`);
    const events = data.events.map(item => `<div class="trace-event"><div>${statusTag(item.status)}</div><div><div class="primary-text">${esc(item.service)} · ${esc(item.operation)}</div><div class="subtle mono">trace_id: ${esc(item.trace_id)}</div></div><div class="trace-duration subtle">${duration(item.duration_ms)}</div></div>`).join('');
    target.innerHTML = section(`调用链详情：${chainId}`, `<div class="panel"><div class="panel-body"><div class="subtle">项目 ${esc(data.project_id)} · ${statusTag(data.status)} · ${data.service_calls} 次内部服务调用</div><div class="trace-detail">${events}</div></div></div>`);
  } catch (error) {
    target.innerHTML = `<div class="panel error"><div class="panel-body">${esc(error.message)}</div></div>`;
  }
}

async function renderUsage() {
  const data = await api('/api/usage');
  const summary = data.summary;
  const rows = data.records.length ? data.records.map(item => `<tr><td>${timeText(item.created_at)}</td><td class="mono">${esc(item.chain_id)}</td><td>${esc(item.project_id)}</td><td>${esc(item.logical_name)}</td><td>${esc(item.endpoint_id || '-')}</td><td>${esc(item.route_reason || '-')}</td><td>${formatNumber(item.input_tokens)} / ${formatNumber(item.output_tokens)} / ${formatNumber(item.total_tokens)}</td><td>${cost(item.estimated_cost)}</td><td>${statusTag(item.status)}</td><td>${duration(item.duration_ms)}</td></tr>`).join('') : '<tr><td colspan="10"><div class="empty">暂无模型调用明细。请先在业务应用提交一次报告生成。</div></td></tr>';
  root.innerHTML = `
    <div class="metrics">
      <article class="metric"><div class="metric-label">近期请求</div><div class="metric-value">${formatNumber(summary.requests)}</div><div class="metric-meta">完成 ${formatNumber(summary.completed)}，失败 ${formatNumber(summary.failed)}</div></article>
      <article class="metric"><div class="metric-label">总 Token</div><div class="metric-value">${formatNumber(summary.total_tokens)}</div><div class="metric-meta">输入 / 输出均在下方明细展示</div></article>
      <article class="metric"><div class="metric-label">估算成本</div><div class="metric-value">${cost(summary.estimated_cost)}</div><div class="metric-meta">按当前场景价格配置计算</div></article>
    </div>
    ${section('近期 LLM 调用明细', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>时间</th><th>Chain ID</th><th>项目</th><th>场景</th><th>Endpoint</th><th>路由原因</th><th>输入 / 输出 / 总 Token</th><th>估算成本</th><th>状态</th><th>耗时</th></tr></thead><tbody>${rows}</tbody></table></div></div>`, '只读运营数据')}
    <p class="notice">${esc(data.notice)}</p>
  `;
}

async function renderModels() {
  const data = await api('/api/resources');
  const disabled = data.management_enabled ? '' : 'disabled';
  const endpointOptions = endpointId => data.endpoints.map(item => `<option value="${esc(item.endpoint_id)}" ${item.endpoint_id === endpointId ? 'selected' : ''}>${esc(item.endpoint_id)} · ${esc(item.model_id)}</option>`).join('');
  const retryOptions = retryLimit => [0, 1, 2, 3].map(value => `<option value="${value}" ${Number(retryLimit) === value ? 'selected' : ''}>${value} 次</option>`).join('');
  const endpointRows = data.endpoints.map(item => {
    const runtime = data.runtime_health[item.endpoint_id] || { status: 'unknown' };
    return `<tr data-endpoint-id="${esc(item.endpoint_id)}"><td class="primary-text">${esc(item.endpoint_id)}</td><td>${esc(item.provider)}</td><td class="mono">${esc(item.model_id)}</td><td class="mono">${esc(item.base_url_env)} / ${esc(item.api_key_env)}</td><td>${statusTag(runtime.status)}<div class="subtle">连续失败 ${formatNumber(runtime.consecutive_failures)}</div></td><td><select class="control-input" data-endpoint-status ${disabled}><option value="enabled" ${item.status === 'enabled' ? 'selected' : ''}>已启用</option><option value="disabled" ${item.status === 'disabled' ? 'selected' : ''}>已停用</option></select></td><td><input class="control-input number-input" data-endpoint-timeout type="number" min="1" value="${item.timeout_ms}" ${disabled}></td><td><button class="action-button" data-save-endpoint type="button" ${disabled}>保存</button></td></tr>`;
  }).join('');
  const routeRows = data.routes.map(item => {
    const latest = data.latest_by_scene[item.logical_name];
    const latestText = latest ? `${latest.endpoint_id || '-'} · ${latest.route_reason || '-'} · ${duration(latest.duration_ms)}` : '暂无真实调用';
    return `<tr data-route-name="${esc(item.logical_name)}"><td><div class="primary-text">${esc(item.display_name)}</div><div class="subtle mono">${esc(item.logical_name)}</div></td><td><select class="control-input" data-route-primary ${disabled}>${endpointOptions(item.primary_endpoint_id)}</select></td><td><select class="control-input" data-route-fallback ${disabled}><option value="">未接入</option>${endpointOptions(item.fallback_endpoint_id)}</select></td><td><select class="control-input" data-route-retry ${disabled}>${retryOptions(item.retry_limit)}</select></td><td><input class="control-input number-input" data-route-input-price type="number" min="0" step="0.000001" value="${item.input_token_price}" ${disabled}> / <input class="control-input number-input" data-route-output-price type="number" min="0" step="0.000001" value="${item.output_token_price}" ${disabled}></td><td><select class="control-input" data-route-status ${disabled}><option value="enabled" ${item.status === 'enabled' ? 'selected' : ''}>已启用</option><option value="disabled" ${item.status === 'disabled' ? 'selected' : ''}>已停用</option></select></td><td class="subtle">${esc(latestText)}</td><td><button class="action-button" data-save-route type="button" ${disabled}>保存</button></td></tr>`;
  }).join('');
  const mode = data.management_enabled ? '管理员配置已加载，保存后下一次调用即时生效。' : '未配置本地管理员密钥，当前为只读展示。';
  root.innerHTML = `${section('Endpoint 资源', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>Endpoint</th><th>提供商</th><th>实际模型</th><th>服务端环境变量</th><th>运行状态</th><th>配置状态</th><th>超时 ms</th><th>操作</th></tr></thead><tbody>${endpointRows}</tbody></table></div></div>`, mode)}${section('逻辑场景与路由策略', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>逻辑场景</th><th>主 Endpoint</th><th>备用 Endpoint</th><th>失败后重试</th><th>输入 / 输出单价（每百万 Token）</th><th>状态</th><th>最近真实路由</th><th>操作</th></tr></thead><tbody>${routeRows}</tbody></table></div></div>`, '仅对连接、限流和 5xx 等可重试错误生效；0 次表示失败后直接尝试备用 Endpoint')}${section('新增 Endpoint', `<form class="panel form-grid" data-endpoint-create><label>Endpoint 标识<input class="control-input" name="endpoint_id" placeholder="ark-report-secondary" ${disabled} required></label><label>提供商<input class="control-input" name="provider" value="ark" ${disabled} required></label><label>实际模型<input class="control-input" name="model_id" placeholder="服务端模型或接入点标识" ${disabled} required></label><label>Base URL 环境变量<input class="control-input" name="base_url_env" value="ARK_BASE_URL" ${disabled} required></label><label>API Key 环境变量<input class="control-input" name="api_key_env" value="ARK_API_KEY" ${disabled} required></label><label>超时 ms<input class="control-input" name="timeout_ms" type="number" min="1" value="60000" ${disabled} required></label><div class="form-action"><button class="action-button" type="submit" ${disabled}>新增 Endpoint</button></div></form>`, '只填写服务端环境变量名，不填写或展示密钥值')}`;
}

async function renderReliability() {
  const data = await api('/api/reliability');
  const reportRoute = data.routes.find(item => item.logical_name === 'report-generation');
  const retryLimit = reportRoute ? Number(reportRoute.retry_limit) : 1;
  const runtimeRows = Object.entries(data.runtime_health).map(([endpointId, runtime]) => `<tr><td class="primary-text">${esc(endpointId)}</td><td>${statusTag(runtime.status)}</td><td>${formatNumber(runtime.consecutive_failures)}</td><td>${esc(runtime.last_error || '-')}</td><td>${esc(runtime.mode === 'local_memory_demo' ? '本地内存演示' : '状态暂不可用')}</td></tr>`).join('') || '<tr><td colspan="5"><div class="empty">暂无 Endpoint 运行状态。</div></td></tr>';
  const recentRows = data.recent_records.length ? data.recent_records.map(item => `<tr><td>${timeText(item.created_at)}</td><td class="mono">${esc(item.chain_id)}</td><td>${esc(item.endpoint_id || '-')}</td><td>${item.streamed ? 'SSE' : '非流式'}</td><td>${item.ttft_ms == null ? '-' : duration(item.ttft_ms)}</td><td>${formatNumber(item.retry_count)}</td><td>${item.fallback_used ? '已回退' : '-'}</td><td>${esc(item.attempted_endpoint_ids || '-')}</td><td>${statusTag(item.status)}</td><td>${duration(item.duration_ms)}</td></tr>`).join('') : '<tr><td colspan="10"><div class="empty">暂无模型调用明细。完成一次报告生成后将在此展示。</div></td></tr>';
  root.innerHTML = `${section('Endpoint 运行状态', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>Endpoint</th><th>熔断状态</th><th>连续失败</th><th>最近错误分类</th><th>状态存储</th></tr></thead><tbody>${runtimeRows}</tbody></table></div></div>`, '运行态负责回退决策；配置启停仍在“模型服务”页管理')}${section('近期可靠性决策', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>时间</th><th>Chain ID</th><th>最终 Endpoint</th><th>模式</th><th>TTFT</th><th>重试次数</th><th>回退</th><th>尝试过的 Endpoint</th><th>结果</th><th>总耗时</th></tr></thead><tbody>${recentRows}</tbody></table></div></div>`, 'TTFT 为网关收到上游首个文本片段的耗时；不保存提示词与模型回答')}${section('运行策略', `<div class="panel"><div class="panel-body"><div class="service-list"><div class="service-row"><div><div class="service-name">有限重试</div><div class="subtle">仅针对连接、限流与 5xx 等可重试错误，对当前 Endpoint 再尝试一次。</div></div><div class="subtle">最多 1 次</div></div><div class="service-row"><div><div class="service-name">熔断与回退</div><div class="subtle">同一 Endpoint 连续 3 次可重试失败后，30 秒内不再继续请求它，并选择已配置的备用 Endpoint。</div></div><div class="subtle">主备路由</div></div><div class="service-row"><div><div class="service-name">流式与 TTFT</div><div class="subtle">浏览器通过业务应用接收 SSE；TTFT 从内部网关转发请求到 LLM Service 收到首个文本片段时开始计量。</div></div><div class="subtle">真实首段</div></div><div class="service-row"><div><div class="service-name">生产落点</div><div class="subtle">当前状态为单实例演示；真实场景通过 Redis 或健康检查组件共享状态和摘流结果。</div></div><div class="subtle">运行时配置</div></div></div></div></div>`, data.notice)}`;
  const retryRow = [...root.querySelectorAll('.service-row')].find(row =>
    row.querySelector('.service-name')?.textContent === '有限重试'
  );
  if (retryRow) {
    const copy = retryRow.querySelector('.subtle');
    const value = retryRow.querySelectorAll('.subtle')[1];
    copy.textContent = '仅针对连接、限流与 5xx 等可重试错误生效；可在“模型服务”中按逻辑场景调整。';
    value.textContent = `当前最多 ${retryLimit} 次`;
  }
}

function verificationEventCopy(event, data) {
  const labels = {
    attempt: '开始请求 Endpoint', meta: '收到模型首段', delta: '透传文本片段', done: '流式调用完成',
    error: '流式调用异常', retry: '当前 Endpoint 重试', fallback: '切换备用 Endpoint',
    circuit_open: '熔断跳过 Endpoint', fault_injected: '本地故障注入生效',
  };
  const detail = data.endpoint_id || data.from_endpoint_id || data.to_endpoint_id || '';
  return `${labels[event] || event}${detail ? ` · ${detail}` : ''}`;
}

function verificationRow(event, data, live = false) {
  const elapsed = data.elapsed_ms == null ? '-' : duration(data.elapsed_ms);
  const detail = event === 'delta'
    ? `已透传 ${formatNumber(data.content_length ?? (data.content || '').length)} 个字符`
    : data.message || data.error_type || data.actual_model || data.to_endpoint_id || '';
  return `<div class="verification-event ${esc(event)}"><div class="event-seq mono">${data.sequence ?? '-'}</div><div><div class="primary-text">${esc(verificationEventCopy(event, data))}</div><div class="subtle">${esc(detail)}</div></div><div class="subtle">${elapsed}</div>${live ? '' : '<div></div>'}</div>`;
}

async function showStreamRun(chainId) {
  const target = document.getElementById('streamRunReplay');
  if (!target || !chainId) return;
  target.innerHTML = '<div class="panel"><div class="empty">正在读取已脱敏的事件回放...</div></div>';
  try {
    const data = await api(`/api/stream-runs/${encodeURIComponent(chainId)}`);
    const rows = data.events.map(item => verificationRow(item.event, item)).join('') || '<div class="empty">尚未收到事件。</div>';
    target.innerHTML = section(`事件回放：${chainId}`, `<div class="panel"><div class="panel-body"><div class="subtle">${data.status === 'completed' ? '完成' : data.status === 'failed' ? '失败' : '进行中'} · 回放不保存模型文本，只保留片段长度与治理决策。</div><div class="verification-timeline">${rows}</div></div></div>`);
  } catch (error) {
    target.innerHTML = `<div class="panel error"><div class="panel-body">${esc(error.message)}</div></div>`;
  }
}

async function renderVerification() {
  const [resources, runs] = await Promise.all([api('/api/resources'), api('/api/stream-runs')]);
  const route = resources.routes.find(item => item.logical_name === 'report-generation');
  const primaryEndpoint = route?.primary_endpoint_id || '';
  const faultDisabled = resources.management_enabled && primaryEndpoint ? '' : 'disabled';
  const history = runs.runs.map(item => `<tr><td><button class="link-button mono" data-stream-run-id="${esc(item.chain_id)}" type="button">${esc(item.chain_id)}</button></td><td>${statusTag(item.status)}</td><td>${formatNumber(item.event_count)}</td><td>${timeText(item.started_at)}</td></tr>`).join('') || '<tr><td colspan="4"><div class="empty">还没有流式验证记录。</div></td></tr>';
  root.innerHTML = `
    ${section('发起一次流式验证', `<div class="panel"><div class="panel-body verification-controls"><label>验证方式<select class="control-input" id="verificationMode"><option value="normal">正常流式透传</option><option value="fail-once">主 Endpoint 模拟失败一次</option><option value="fail-three">主 Endpoint 连续模拟失败三次</option><option value="abort">首段返回后模拟流中断</option></select></label><button class="refresh-button" data-start-verification type="button">发起验证</button><button class="action-button" data-clear-fault type="button" ${faultDisabled}>清除本地故障</button></div><p class="notice">正常验证会发送一条很短的真实模型请求。故障模式仅在本地 LLM Service 内存中生效，失败发生在上游调用前；首段中断模式会先收到真实首段文本。</p></div>`, primaryEndpoint ? `当前主 Endpoint：${primaryEndpoint}` : '当前未找到 report-generation 主 Endpoint')}
    ${section('实时事件', `<div class="panel"><div class="panel-body"><div id="verificationState" class="verification-state">等待发起验证</div><div id="verificationLive" class="verification-timeline"><div class="empty">事件会按收到顺序出现在这里。</div></div><div class="stream-output"><div class="subtle">实时透传文本</div><pre id="verificationOutput">尚未开始</pre></div></div></div>`, '展示真实 SSE 事件；完整文本只在当前页面实时展示')}
    <div id="streamRunReplay"></div>
    ${section('近期验证', `<div class="panel"><div class="table-wrap"><table><thead><tr><th>Chain ID</th><th>结果</th><th>事件数</th><th>开始时间</th></tr></thead><tbody>${history}</tbody></table></div></div>`, '网关进程重启后自动清空')}
  `;
  if (requestedStreamRun) showStreamRun(requestedStreamRun);
}

async function startVerification() {
  const button = root.querySelector('[data-start-verification]');
  const mode = root.querySelector('#verificationMode')?.value || 'normal';
  const state = root.querySelector('#verificationState');
  const live = root.querySelector('#verificationLive');
  const output = root.querySelector('#verificationOutput');
  const routeData = await api('/api/resources');
  const route = routeData.routes.find(item => item.logical_name === 'report-generation');
  const endpointId = route?.primary_endpoint_id;
  const mappings = {
    'fail-once': { mode: 'connection_failure', times: 1 },
    'fail-three': { mode: 'connection_failure', times: 3 },
    abort: { mode: 'abort_after_first_delta', times: 1 },
  };
  button.disabled = true;
  live.innerHTML = '';
  output.textContent = '';
  state.textContent = '正在建立流式连接...';
  try {
    if (mappings[mode]) {
      if (!endpointId) throw new Error('当前未配置主 Endpoint，无法注入验证故障。');
      await api('/api/fault-injection', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ endpoint_id: endpointId, ...mappings[mode] }) });
    }
    const response = await fetch('/api/stream-verification', { method: 'POST' });
    if (!response.ok || !response.body) throw new Error('无法启动流式验证。');
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let chainId = '';
    let finalStatus = 'running';
    const accept = (event, raw) => {
      let data;
      try { data = JSON.parse(raw); } catch (_) { return; }
      if (event === 'verification') {
        chainId = data.chain_id;
        state.textContent = `验证进行中：${chainId}`;
        return;
      }
      if (event === 'delta') output.textContent += data.content || '';
      if (event === 'done') finalStatus = 'completed';
      if (event === 'error') finalStatus = 'failed';
      const row = document.createElement('div');
      row.innerHTML = verificationRow(event, data, true);
      live.appendChild(row.firstChild);
    };
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = 'message'; let raw = '';
        block.split('\n').forEach(line => { if (line.startsWith('event:')) event = line.slice(6).trim(); if (line.startsWith('data:')) raw += line.slice(5).trim(); });
        if (raw) accept(event, raw);
      }
      if (done) break;
    }
    state.textContent = finalStatus === 'completed' ? `验证完成：${chainId}` : `验证结束：${chainId}`;
    if (chainId) await showStreamRun(chainId);
  } catch (error) {
    state.textContent = `验证失败：${error.message}`;
  } finally {
    button.disabled = false;
  }
}

async function renderCapabilities() {
  const data = await api('/api/capabilities');
  const rows = data.capabilities.map(item => `<div class="capability-row"><div class="primary-text">${esc(item.name)}</div><div>${statusTag(item.status)}</div><div class="subtle">${esc(item.description)}</div></div>`).join('');
  root.innerHTML = section('能力状态', `<div class="panel"><div class="capability-list">${rows}</div></div>`, '与企业级 AI 网关方案对比分析保持一致');
}

async function renderApps() {
  root.innerHTML = section('已接入业务应用', `<div class="app-grid"><article class="app-card"><h3>ODM 测试报告与问题复盘助手</h3><p>通过内部 MCP 服务网关调用 Memory Service、Prompt Hub 与 LLM Service，作为端到端调用链验证案例。</p><footer>${statusTag('implemented')}<a class="open-app" href="http://127.0.0.1:8502" target="_blank" rel="noopener">打开应用</a></footer></article></div>`);
}

const renderers = { overview: renderOverview, external: renderExternal, services: renderServices, projects: renderProjects, traces: renderTraces, usage: renderUsage, models: renderModels, reliability: renderReliability, verification: renderVerification, capabilities: renderCapabilities, apps: renderApps };

async function render(view = currentView) {
  const sequence = ++renderSequence;
  currentView = view;
  const config = views[view];
  pageTitle.textContent = config.title;
  viewName.textContent = config.label;
  navList.querySelectorAll('.nav-item').forEach(item => item.classList.toggle('active', item.dataset.view === view));
  loading();
  try {
    await renderers[view]();
    if (sequence !== renderSequence) return render(currentView);
  } catch (error) {
    if (sequence === renderSequence) failure(error.message);
  }
}

navList.addEventListener('click', event => {
  const button = event.target.closest('[data-view]');
  if (button) render(button.dataset.view);
});

root.addEventListener('click', async event => {
  const viewLink = event.target.closest('[data-view-link]');
  if (viewLink) return render(viewLink.dataset.viewLink);
  const trace = event.target.closest('[data-trace-id]');
  if (trace) return showTrace(trace.dataset.traceId);
  if (event.target.closest('[data-health-check]')) {
    event.target.disabled = true;
    try { await api('/api/services/health-check', { method: 'POST' }); } finally { await render('services'); }
  }
  const streamRun = event.target.closest('[data-stream-run-id]');
  if (streamRun) return showStreamRun(streamRun.dataset.streamRunId);
  if (event.target.closest('[data-start-verification]')) return startVerification();
  if (event.target.closest('[data-clear-fault]')) {
    await api('/api/fault-injection', { method: 'DELETE' });
    return render('verification');
  }
  const projectButton = event.target.closest('[data-save-project]');
  if (projectButton) {
    const row = projectButton.closest('tr');
    return saveManagement(`/api/projects/${encodeURIComponent(row.dataset.projectId)}`, 'PATCH', {
      status: row.querySelector('[data-project-status]').value,
      daily_token_limit: Number(row.querySelector('[data-project-limit]').value),
    }, 'projects');
  }
  const subscriptionButton = event.target.closest('[data-save-subscription]');
  if (subscriptionButton) {
    const row = subscriptionButton.closest('tr');
    return saveManagement(`/api/projects/${encodeURIComponent(row.dataset.subscriptionProject)}/subscriptions/${encodeURIComponent(row.dataset.subscriptionScene)}`, 'PUT', {
      status: row.querySelector('[data-subscription-status]').value,
    }, 'projects');
  }
  const endpointButton = event.target.closest('[data-save-endpoint]');
  if (endpointButton) {
    const row = endpointButton.closest('tr');
    return saveManagement(`/api/resources/endpoints/${encodeURIComponent(row.dataset.endpointId)}`, 'PATCH', {
      status: row.querySelector('[data-endpoint-status]').value,
      timeout_ms: Number(row.querySelector('[data-endpoint-timeout]').value),
    }, 'models');
  }
  const routeButton = event.target.closest('[data-save-route]');
  if (routeButton) {
    const row = routeButton.closest('tr');
    return saveManagement(`/api/resources/routes/${encodeURIComponent(row.dataset.routeName)}`, 'PATCH', {
      primary_endpoint_id: row.querySelector('[data-route-primary]').value,
      fallback_endpoint_id: row.querySelector('[data-route-fallback]').value || null,
      input_token_price: Number(row.querySelector('[data-route-input-price]').value),
      output_token_price: Number(row.querySelector('[data-route-output-price]').value),
      retry_limit: Number(row.querySelector('[data-route-retry]').value),
      status: row.querySelector('[data-route-status]').value,
    }, 'models');
  }
});

root.addEventListener('submit', async event => {
  const form = event.target.closest('[data-endpoint-create]');
  if (!form) return;
  event.preventDefault();
  const values = new FormData(form);
  return saveManagement('/api/resources/endpoints', 'POST', {
    endpoint_id: values.get('endpoint_id'),
    provider: values.get('provider'),
    model_id: values.get('model_id'),
    base_url_env: values.get('base_url_env'),
    api_key_env: values.get('api_key_env'),
    timeout_ms: Number(values.get('timeout_ms')),
  }, 'models');
});

async function saveManagement(path, method, body, view) {
  try {
    await api(path, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    await render(view);
  } catch (error) {
    failure(error.message);
  }
}

refreshButton.addEventListener('click', async () => {
  refreshButton.disabled = true;
  try { await render(); } finally { refreshButton.disabled = false; }
});

render();
