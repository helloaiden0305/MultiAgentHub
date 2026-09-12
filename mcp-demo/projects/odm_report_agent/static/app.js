let sessionId = null;
const msgBox  = document.getElementById('messages');
const input   = document.getElementById('input');
const sendBtn = document.getElementById('sendBtn');
const styleSelect = document.getElementById('styleSelect');
const sessionInfo = document.getElementById('sessionInfo');

/* ---- 快捷键 ---- */
input.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 140) + 'px';
});

document.querySelectorAll('[data-example]').forEach(button => {
  button.addEventListener('click', () => {
    input.value = button.dataset.example || '';
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 140) + 'px';
    input.focus();
  });
});

/* ---- 输出风格切换 ---- */
styleSelect.addEventListener('change', async () => {
  const style = styleSelect.value;
  try {
    const res = await fetch('/api/style', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ style, session_id: sessionId }),
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    sessionId = data.session_id;
    addSystemMsg('输出风格已切换为: ' + style);
  } catch (e) {
    alert('切换输出风格失败: ' + e);
  }
});

/* ---- 工具函数 ---- */
function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function scrollDown() {
  requestAnimationFrame(() => { msgBox.scrollTop = msgBox.scrollHeight; });
}

function setLock(locked) {
  sendBtn.disabled = locked;
  input.disabled = locked;
}

function addMsg(role, html) {
  const wrap = document.createElement('div');
  wrap.className = 'msg ' + role;

  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'user' ? '你' : '助手';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = html;

  wrap.appendChild(label);
  wrap.appendChild(bubble);
  msgBox.appendChild(wrap);
  scrollDown();
  return wrap;
}

function addSystemMsg(text) {
  const el = document.createElement('div');
  el.className = 'msg system';
  el.innerHTML = '<div class="bubble">' + esc(text) + '</div>';
  msgBox.appendChild(el);
  scrollDown();
}

function appendTrace(message, trace, chainId) {
  if (!trace || !Array.isArray(trace.steps) || !chainId) return;

  const completed = trace.status === 'completed';
  const details = document.createElement('details');
  details.className = 'trace-card ' + (completed ? 'completed' : 'failed');

  const summary = document.createElement('summary');
  summary.innerHTML =
    '<span class="trace-title">本次调用轨迹</span>' +
    '<span class="trace-status">' + (completed ? '已完成' : '未完成') + '</span>' +
    '<span class="trace-duration">' + Number(trace.total_duration_ms || 0) + 'ms</span>';
  details.appendChild(summary);

  const gatewayNote = document.createElement('div');
  gatewayNote.className = 'trace-gateway-note';
  gatewayNote.textContent = '内部服务网关统一接入：鉴权、路由与审计';
  details.appendChild(gatewayNote);

  const steps = document.createElement('div');
  steps.className = 'trace-steps';
  const serviceLabels = {
    'memory-service': 'Memory Service',
    'prompt-hub': 'Prompt Hub',
    'llm-gateway': 'LLM Gateway',
  };
  trace.steps.forEach(step => {
    const row = document.createElement('div');
    row.className = 'trace-step ' + (step.status || 'failed');
    const status = step.status === 'completed' ? '完成' : '失败';
    const serviceLabel = serviceLabels[step.service] || step.service || '服务';
    const actionLabel = step.label || '服务调用';
    row.innerHTML =
      '<span class="trace-step-status">' + status + '</span>' +
      '<span class="trace-step-copy">' +
        '<span class="trace-step-service">' + esc(serviceLabel) + '</span>' +
        '<span class="trace-step-label">' + esc(actionLabel) + '</span>' +
      '</span>' +
      '<span class="trace-step-duration">' + Number(step.duration_ms || 0) + 'ms</span>';
    if (step.message) {
      const reason = document.createElement('div');
      reason.className = 'trace-step-message';
      reason.textContent = step.message;
      row.appendChild(reason);
    }
    steps.appendChild(row);
  });
  details.appendChild(steps);

  const chain = document.createElement('div');
  chain.className = 'trace-chain';
  chain.innerHTML = '链路标识：<code>' + esc(chainId) + '</code> · <a href="http://127.0.0.1:8500/?stream_run=' + encodeURIComponent(chainId) + '" target="_blank" rel="noopener">在服务台查看流式事件</a>';
  details.appendChild(chain);

  message.appendChild(details);
  scrollDown();
}

/* ---- 发送消息 ---- */
async function send() {
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  input.style.height = 'auto';

  addMsg('user', esc(text));

  const typing = document.createElement('div');
  typing.className = 'msg bot typing';
  typing.innerHTML = '<div class="label">助手</div><div class="bubble">正在通过 Gateway 调用 Memory、Prompt、LLM...</div>';
  msgBox.appendChild(typing);
  scrollDown();
  setLock(true);

  try {
    const res = await fetch('/api/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    if (!res.ok) {
      throw new Error('流式服务请求失败 (HTTP ' + res.status + ')');
    }
    if (!res.body) throw new Error('浏览器未收到流式响应');

    const bubble = typing.querySelector('.bubble');
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let reply = '';
    let chainId = '';
    let completed = false;

    const handleEvent = (event, rawData) => {
      let data;
      try { data = JSON.parse(rawData); } catch (_) { return; }
      if (event === 'session') {
        sessionId = data.session_id || sessionId;
        chainId = data.chain_id || chainId;
        sessionInfo.textContent = '会话：' + sessionId;
      } else if (event === 'meta') {
        bubble.textContent = '模型已开始响应，正在流式生成...';
      } else if (event === 'delta') {
        typing.classList.remove('typing');
        reply += data.content || '';
        bubble.textContent = reply;
        scrollDown();
      } else if (event === 'done') {
        completed = true;
        typing.classList.remove('typing');
        bubble.textContent = data.reply || reply;
        appendTrace(typing, data.trace, chainId);
      } else if (event === 'error') {
        typing.classList.remove('typing');
        if (!reply) bubble.innerHTML = '<span class="error-text">' + esc(data.error || '生成失败，请稍后重试。') + '</span>';
        appendTrace(typing, data.trace, chainId);
      }
    };

    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        let event = 'message';
        let rawData = '';
        block.split('\n').forEach(line => {
          if (line.startsWith('event:')) event = line.slice(6).trim();
          if (line.startsWith('data:')) rawData += line.slice(5).trim();
        });
        if (rawData) handleEvent(event, rawData);
      }
      if (done) break;
    }
    if (!completed && !reply) throw new Error('流式响应未完成');
  } catch (e) {
    typing.classList.remove('typing');
    typing.querySelector('.bubble').innerHTML =
      '<span class="error-text">请求失败，请检查服务状态后重试。</span>';
  }

  setLock(false);
  scrollDown();
  input.focus();
}

/* ---- 清空记忆 ---- */
async function clearMemory() {
  if (!sessionId) { alert('还没有开始对话'); return; }
  try {
    const res = await fetch('/api/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    addSystemMsg(data.msg);
  } catch (e) {
    alert('清空失败: ' + e);
  }
}

/* ---- 用户画像 ---- */
async function showProfile() {
  if (!sessionId) { alert('还没有开始对话'); return; }
  try {
    const res = await fetch('/api/profile', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const data = await res.json();
    const facts = data.facts || [];

    let html = '<h4>工程上下文</h4>';
    if (facts.length === 0) {
      html += '<div>暂无工程上下文。</div>';
    } else {
      html += '<ul>' + facts.map(f =>
        '<li><b>' + esc(f.key) + '</b>: ' + esc(f.value) +
        (f.source ? ' <i>(来源: ' + esc(f.source) + ')</i>' : '') +
        '</li>'
      ).join('') + '</ul>';
    }

    const el = document.createElement('div');
    el.className = 'profile-panel';
    el.innerHTML = html;
    msgBox.appendChild(el);
    scrollDown();
  } catch (e) {
    alert('获取画像失败: ' + e);
  }
}

/* ---- 新建会话 ---- */
function newSession() {
  sessionId = null;
  msgBox.innerHTML = '';
  addMsg('bot', '提供测试记录、缺陷现象或日志摘要后，我会生成结构化报告、复盘初稿、风险说明或回归验证记录。');
  sessionInfo.textContent = '会话：未开始';
  input.focus();
}

input.focus();
