/* ─── State ─────────────────────────────────────────────────── */
let chats           = {};
let cid             = null;
let busy            = false;
let theme           = localStorage.getItem('gl-theme') || 'dark';
let activeTaskId    = null;
let activeEventSource = null;

/* ─── Boot ───────────────────────────────────────────────────── */
function boot() {
  applyTheme(theme);
  try {
    const s = localStorage.getItem('gl-chats');
    if (s) chats = JSON.parse(s);
  } catch {}
  newChat();
}

/* ─── Theme ──────────────────────────────────────────────────── */
function applyTheme(t) {
  theme = t;
  document.documentElement.setAttribute('data-theme', t);
  localStorage.setItem('gl-theme', t);
  const hl = document.getElementById('hljs-theme');
  if (t === 'dark') {
    document.getElementById('theme-icon').textContent = '☀️';
    document.getElementById('theme-lbl').textContent  = 'Light mode';
    hl.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-dark.min.css';
  } else {
    document.getElementById('theme-icon').textContent = '🌙';
    document.getElementById('theme-lbl').textContent  = 'Dark mode';
    hl.href = 'https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.9.0/styles/atom-one-light.min.css';
  }
}

function toggleTheme() { applyTheme(theme === 'dark' ? 'light' : 'dark'); }

/* ─── Sidebar tab dispatcher ─────────────────────────────────── */
function setSidebarTab(tab) {
  if (tab !== 'tasks' && typeof tasksMode !== 'undefined' && tasksMode) {
    exitTasksMode();
  }
  if (tab === 'tasks') {
    enterTasksMode();
    return;
  }
  if (typeof _setSidebarTabVault === 'function') _setSidebarTabVault(tab);
}

/* ─── Layout mode helpers ────────────────────────────────────── */

function setWelcomeMode() {
  const chatMain      = document.getElementById('chat-main');
  const inputArea     = document.getElementById('input-area');
  const welcomeCenter = document.getElementById('welcome-center');
  const welcomeSuggs  = document.getElementById('welcome-suggs');

  welcomeCenter.insertBefore(inputArea, welcomeSuggs);
  inputArea.style.transform  = '';
  inputArea.style.transition = '';
  chatMain.classList.add('is-welcome');
  chatMain.classList.remove('is-chat');
  document.getElementById('msgs-container').innerHTML = '';
  updateHeader();
}

function enterChatMode() {
  const inputArea = document.getElementById('input-area');
  const inputSlot = document.getElementById('input-slot');
  const chatMain  = document.getElementById('chat-main');

  const before = inputArea.getBoundingClientRect();
  inputSlot.appendChild(inputArea);
  chatMain.classList.remove('is-welcome');
  chatMain.classList.add('is-chat');
  void inputArea.offsetWidth;

  const after = inputArea.getBoundingClientRect();
  const dy = before.top  - after.top;
  const dx = before.left - after.left;
  inputArea.style.transition = 'none';
  inputArea.style.transform  = `translate(${dx}px, ${dy}px)`;
  void inputArea.offsetWidth;
  inputArea.style.transition = 'transform 0.48s cubic-bezier(0.4, 0, 0.2, 1)';
  inputArea.style.transform  = 'translate(0, 0)';
  setTimeout(() => {
    inputArea.style.transition = '';
    inputArea.style.transform  = '';
  }, 520);
}

function enterChatModeInstant() {
  const inputArea = document.getElementById('input-area');
  const inputSlot = document.getElementById('input-slot');
  const chatMain  = document.getElementById('chat-main');

  inputSlot.appendChild(inputArea);
  inputArea.style.transform  = '';
  inputArea.style.transition = '';
  chatMain.classList.remove('is-welcome');
  chatMain.classList.add('is-chat');
}

/* ─── Chat lifecycle ─────────────────────────────────────────── */
function newChat() {
  const id = Date.now().toString();
  chats[id] = { title: 'New Conversation', messages: [], ts: Date.now() };
  cid = id;
  renderHistory();
  setWelcomeMode();
  document.getElementById('msg-input').focus();
}

function openChat(id) {
  // Reindex the conversation we're leaving before switching
  if (cid && cid !== id && chats[cid] && chats[cid].messages.length > 0) {
    fetch(`/api/conversations/${cid}/reindex`, { method: 'POST' }).catch(() => {});
  }

  cid = id;
  const msgs = chats[id].messages;
  if (msgs.length > 0) {
    enterChatModeInstant();
    document.getElementById('msgs-container').innerHTML = '';
    msgs.forEach(m => appendMsg(m.role, m.content, false));
    decorate(document.getElementById('msgs-container'));
    scrollBot();
  } else {
    setWelcomeMode();
  }

  renderHistory();
  updateHeader();
}

function updateHeader() {
  const c = chats[cid];
  if (c) document.getElementById('chat-title').textContent = c.title;
}

/* ─── History ────────────────────────────────────────────────── */
function renderHistory() {
  const list = document.getElementById('hist-list');
  list.innerHTML = Object.entries(chats)
    .sort((a, b) => b[1].ts - a[1].ts)
    .map(([id, c]) => `
      <div class="hist-item ${id === cid ? 'active' : ''}" onclick="openChat('${id}')">
        <div class="hist-dot"></div>
        <div class="hist-title">${esc(c.title)}</div>
        <button class="hist-del" onclick="deleteChat('${id}', event)" title="Delete">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <path d="M18 6 6 18M6 6l12 12"/>
          </svg>
        </button>
      </div>`)
    .join('');
}

function deleteChat(id, e) {
  e.stopPropagation();

  // Remove from Glass Harness sessions + RAG index
  fetch(`/api/conversations/${id}`, { method: 'DELETE' }).catch(() => {});

  delete chats[id];
  save();

  if (cid === id) {
    const remaining = Object.keys(chats).sort((a, b) => chats[b].ts - chats[a].ts);
    if (remaining.length > 0) {
      openChat(remaining[0]);
    } else {
      newChat();
    }
  } else {
    renderHistory();
  }
}

/* ─── Conversation persistence ───────────────────────────────── */
function persistConversation() {
  const data = chats[cid];
  if (!data) return;
  fetch(`/api/conversations/${cid}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  }).catch(() => {});
}

/* ─── Messages ───────────────────────────────────────────────── */
function appendMsg(role, content, animate = true) {
  const el = document.createElement('div');
  el.className = `msg ${role}`;
  if (!animate) el.style.animation = 'none';

  if (role === 'user') {
    el.innerHTML = `<div class="user-pill">${userHTML(content)}</div>`;
  } else {
    el.innerHTML = `
      <div class="ai-who">
        <div class="av-ai">✦</div>
        <span class="ai-name">Glass Harness</span>
      </div>
      <div class="ai-body">${mdHTML(content)}</div>`;
  }

  document.getElementById('msgs-container').appendChild(el);
  return el;
}

function showTyping(label = 'Thinking…') {
  let el = document.getElementById('typing');
  if (!el) {
    el = document.createElement('div');
    el.id = 'typing';
    el.innerHTML = `
      <div class="typing-row">
        <div class="ai-who" style="padding:0">
          <div class="av-ai">✦</div>
          <span class="ai-name">Glass Harness</span>
        </div>
      </div>
      <div class="typing-status">
        <div class="typing-dots">
          <div class="t-dot"></div><div class="t-dot"></div><div class="t-dot"></div>
        </div>
        <span class="typing-label" id="typing-label"></span>
      </div>`;
    el.style.paddingBottom = '16px';
    document.getElementById('msgs-container').appendChild(el);
  }
  const lbl = document.getElementById('typing-label');
  if (lbl) lbl.textContent = label;
  scrollBot();
}

function hideTyping() {
  const t = document.getElementById('typing');
  if (t) t.remove();
}

/* ─── Cancel running task ────────────────────────────────────── */
function cancelTask() {
  if (!activeTaskId) return;
  const tid = activeTaskId;
  if (activeEventSource) { activeEventSource.close(); activeEventSource = null; }
  activeTaskId = null;
  fetch(`/api/agent/tasks/${tid}`, { method: 'DELETE' }).catch(() => {});
  hideTyping();
  busy = false;
  document.getElementById('send-btn').disabled = false;
}

/* ─── Send → Glass Harness ───────────────────────────────────── */
async function send() {
  if (busy) return;
  const inp  = document.getElementById('msg-input');
  const text = inp.value.trim();
  if (!text) return;

  inp.value = '';
  resize(inp);

  const chat    = chats[cid];
  const isFirst = chat.messages.length === 0;

  chat.messages.push({ role: 'user', content: text });

  if (isFirst) {
    chat.title = text.slice(0, 46) + (text.length > 46 ? '…' : '');
    updateHeader();
    enterChatMode();
  }

  appendMsg('user', text);
  renderHistory();
  save();
  scrollBot();

  busy = true;
  document.getElementById('send-btn').disabled = true;
  showTyping('Thinking…');

  // ── Queue task on Glass Harness ───────────────────────────────
  let taskId;
  try {
    const r = await fetch('/api/agent/queue', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt: text, session: cid }),
    });
    const data = await r.json();
    if (!r.ok || !data.task_id) throw new Error(data.error || 'Queue failed');
    taskId = data.task_id;
  } catch (e) {
    hideTyping();
    const errEl = appendMsg('assistant', '');
    errEl.querySelector('.ai-body').innerHTML =
      `<span style="color:var(--text-muted)">Could not reach Glass Harness — ${esc(e.message)}</span>`;
    chat.messages.push({ role: 'assistant', content: '' });
    busy = false;
    document.getElementById('send-btn').disabled = false;
    return;
  }

  activeTaskId = taskId;

  // Reserve a slot in the messages array; DOM element created lazily on first event
  chat.messages.push({ role: 'assistant', content: '' });

  // ── Open SSE stream ───────────────────────────────────────────
  const es = new EventSource(`/api/agent/stream/${taskId}`);
  activeEventSource = es;

  let outputBuf    = '';
  let raf          = null;
  let msgEl        = null;
  let body         = null;
  let thinkDone    = false;
  let lastStepEl   = null;

  // Create the assistant bubble (once) — includes a hidden thinking block
  function ensureBubble() {
    if (msgEl) return;
    hideTyping();
    msgEl = document.createElement('div');
    msgEl.className = 'msg assistant';
    msgEl.innerHTML = `
      <div class="ai-who">
        <div class="av-ai">✦</div>
        <span class="ai-name">Glass Harness</span>
      </div>
      <div class="think-block" style="display:none">
        <div class="think-header" onclick="this.closest('.think-block').classList.toggle('collapsed')">
          <span class="think-title">Thinking…</span>
          <svg class="think-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none"
               stroke="currentColor" stroke-width="2.5" stroke-linecap="round">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </div>
        <div class="think-body">
          <div class="think-steps"></div>
          <div class="think-done" hidden>
            <div class="done-check">✓</div>
            <span class="done-text">Done</span>
          </div>
        </div>
      </div>
      <div class="ai-body"></div>`;
    document.getElementById('msgs-container').appendChild(msgEl);
    body = msgEl.querySelector('.ai-body');
    body.classList.add('streaming-cursor');
    scrollBot();
  }

  // Append one step to the thinking timeline
  function addThinkingStep(type, rawText) {
    ensureBubble();

    // Detect sub-type from prefix, then strip all bracket prefixes for display
    const isPlan  = /^\[plan\]/i.test(rawText);
    const isShell = type === 'shell' || /^\[shell\]/i.test(rawText);
    const displayText = rawText
      .replace(/^\[(?:work|plan|thinking|agent|replanner|blocked|shell|skill[^\]]*)\]\s*/i, '')
      .trim();
    if (!displayText) return;

    const icon = isShell ? '<span class="si-shell">$</span>'
               : isPlan  ? '<span class="si-plan">→</span>'
               :            '<span class="si-work">⏱</span>';

    const block = msgEl.querySelector('.think-block');
    block.style.display = '';
    const step = document.createElement('div');
    step.className = `think-step ${isShell ? 'shell' : isPlan ? 'plan' : 'work'}`;
    step.innerHTML = `<div class="step-icon">${icon}</div>`
                   + `<div class="step-content">`
                   +   `<div class="step-text">${esc(displayText)}</div>`
                   + `</div>`;
    block.querySelector('.think-steps').appendChild(step);
    lastStepEl = step;
    scrollBot();
  }

  // Attach a detail line to the last step (collapsible, shown on click)
  function addDetailLine(text) {
    if (!lastStepEl) return;
    let detail = lastStepEl.querySelector('.step-detail');
    if (!detail) {
      detail = document.createElement('div');
      detail.className = 'step-detail';
      lastStepEl.querySelector('.step-content').appendChild(detail);
      // Make the step clickable to toggle detail (capture the element, not the variable)
      const stepEl = lastStepEl;
      stepEl.classList.add('has-detail');
      stepEl.querySelector('.step-text').onclick = (e) => {
        e.stopPropagation();
        stepEl.classList.toggle('expanded');
      };
    }
    const line = document.createElement('div');
    line.className = 'detail-line';
    line.textContent = text;
    detail.appendChild(line);
    scrollBot();
  }

  // Seal the thinking section and collapse it
  function sealThinking() {
    if (thinkDone || !msgEl) return;
    thinkDone = true;
    const block = msgEl.querySelector('.think-block');
    if (!block || block.style.display === 'none') return;
    block.querySelector('.think-done').hidden = false;
    block.querySelector('.think-title').textContent = 'Thought process';
    block.classList.add('collapsed');
  }

  es.onmessage = (e) => {
    let event;
    try { event = JSON.parse(e.data); } catch { return; }
    const { type, content } = event;

    // Work / shell events → add a step to the thinking timeline
    if (type === 'work')  addThinkingStep('work',  content);
    if (type === 'shell') addThinkingStep('shell', content);
    // Detail lines → collapsible output attached to the last step
    if (type === 'detail') addDetailLine(content);

    // Output → seal thinking, stream text into response bubble
    if (type === 'output') {
      ensureBubble();
      sealThinking();
      outputBuf += content + '\n';
      if (!raf) {
        raf = requestAnimationFrame(() => {
          body.innerHTML = marked.parse(outputBuf.trim());
          scrollBot();
          raf = null;
        });
      }
    }

    if (type === 'done') {
      if (raf) cancelAnimationFrame(raf);
      ensureBubble();
      sealThinking();
      body.classList.remove('streaming-cursor');
      const finalText = outputBuf.trim();
      body.innerHTML  = mdHTML(finalText);
      decorate(body);

      chat.messages[chat.messages.length - 1].content = finalText;
      save();
      persistConversation();

      es.close();
      activeEventSource = null;
      activeTaskId      = null;
      busy = false;
      document.getElementById('send-btn').disabled = false;
      scrollBot();
    }

    if (type === 'error') {
      if (raf) cancelAnimationFrame(raf);
      ensureBubble();
      sealThinking();
      body.classList.remove('streaming-cursor');
      const errText = outputBuf.trim() || content || 'Task failed.';
      body.innerHTML = mdHTML(errText) ||
        `<span style="color:var(--text-muted)">${esc(content || 'Task failed.')}</span>`;
      decorate(body);

      chat.messages[chat.messages.length - 1].content = errText;
      save();

      es.close();
      activeEventSource = null;
      activeTaskId      = null;
      busy = false;
      document.getElementById('send-btn').disabled = false;
      scrollBot();
    }
  };

  es.onerror = () => {
    if (raf) cancelAnimationFrame(raf);
    ensureBubble();
    sealThinking();
    body.classList.remove('streaming-cursor');
    if (outputBuf.trim()) {
      body.innerHTML = mdHTML(outputBuf.trim());
      decorate(body);
      chat.messages[chat.messages.length - 1].content = outputBuf.trim();
      save();
    } else {
      body.innerHTML = '<span style="color:var(--text-muted)">Connection lost.</span>';
    }
    es.close();
    activeEventSource = null;
    activeTaskId      = null;
    busy = false;
    document.getElementById('send-btn').disabled = false;
  };
}

function useChip(btn) {
  document.getElementById('msg-input').value = btn.textContent;
  send();
}

/* ─── Utilities ──────────────────────────────────────────────── */
function onKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
}

function resize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 160) + 'px';
}

function scrollBot() {
  const w = document.getElementById('msgs-wrap');
  w.scrollTop = w.scrollHeight;
}

function save() {
  try { localStorage.setItem('gl-chats', JSON.stringify(chats)); } catch {}
}

function esc(s) {
  return String(s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function userHTML(text) {
  return esc(text).replace(/\n/g, '<br>');
}

function mdHTML(text) {
  if (!text) return '';
  try { return marked.parse(text); } catch { return esc(text).replace(/\n/g, '<br>'); }
}

function decorate(container) {
  renderLinkBlocks(container);  // must run before hljs so link blocks are removed first
  container.querySelectorAll('pre code:not(.hljs)').forEach(el => {
    try { hljs.highlightElement(el); } catch {}
  });
  container.querySelectorAll('pre:not([data-cp])').forEach(pre => {
    pre.setAttribute('data-cp', '1');
    const btn = document.createElement('button');
    btn.className = 'copy-btn';
    btn.textContent = 'Copy';
    btn.onclick = () => {
      const code = pre.querySelector('code');
      navigator.clipboard.writeText(code ? code.innerText : pre.innerText);
      btn.textContent = 'Copied!';
      setTimeout(() => { btn.textContent = 'Copy'; }, 2000);
    };
    pre.appendChild(btn);
  });
}

/* ─── Document link widgets ───────────────────────────────────────────────── */

function renderLinkBlocks(container) {
  container.querySelectorAll('pre > code.language-link').forEach(code => {
    const pre  = code.parentElement;
    const path = code.innerText.trim();
    if (!path) return;
    const name = path.split('/').pop() || path;
    const dir  = path.slice(0, path.length - name.length) || '/';

    const card = document.createElement('div');
    card.className = 'doc-link-card';
    card.innerHTML = `
      <div class="dlc-left">
        <svg class="dlc-icon" width="18" height="18" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
          <line x1="16" y1="13" x2="8" y2="13"/>
          <line x1="16" y1="17" x2="8" y2="17"/>
          <line x1="10" y1="9" x2="8" y2="9"/>
        </svg>
        <div class="dlc-info">
          <div class="dlc-title">${esc(name)}</div>
          <div class="dlc-date">${esc(dir)}</div>
        </div>
      </div>
      <button class="dlc-open-btn">Open</button>`;

    card.querySelector('.dlc-open-btn').addEventListener('click', function() {
      openDocPanel(path, this);
    });
    pre.replaceWith(card);
  });
}

/* ─── Doc split panel ────────────────────────────────────────────────────── */

let _activeDocBtn = null;

async function openDocPanel(path, btn) {
  // Deactivate previous button
  if (_activeDocBtn && _activeDocBtn !== btn) {
    _activeDocBtn.classList.remove('dlc-opened');
    _activeDocBtn.textContent = 'Open';
  }
  _activeDocBtn = btn;
  btn.classList.add('dlc-opened');

  const name = path.split('/').pop() || path;
  document.getElementById('doc-panel-title').textContent = name;
  document.getElementById('doc-panel-content').innerHTML =
    '<div class="doc-panel-loading">Loading…</div>';
  document.querySelector('.app').classList.add('doc-open');

  try {
    const r = await fetch(`/api/workspace/file?path=${encodeURIComponent(path)}`);
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
    const data = await r.json();

    // Update card date once we have it
    const dateStr = data.modified_at
      ? new Date(data.modified_at).toLocaleString('en-US', {
          month: 'short', day: 'numeric',
          hour: 'numeric', minute: '2-digit'
        })
      : '';
    const card = btn.closest('.doc-link-card');
    if (card && dateStr) {
      const dateEl = card.querySelector('.dlc-date');
      if (dateEl && !dateEl.dataset.resolved) {
        dateEl.textContent = dateStr;
        dateEl.dataset.resolved = '1';
      }
    }

    const ext = data.name.split('.').pop().toLowerCase();
    const content = document.getElementById('doc-panel-content');
    if (['md', 'markdown', 'txt'].includes(ext)) {
      content.innerHTML = mdHTML(data.content);
      decorate(content);
    } else {
      content.innerHTML = `<pre><code>${esc(data.content)}</code></pre>`;
      decorate(content);
    }
    document.getElementById('doc-panel-title').textContent = data.name;
  } catch (e) {
    document.getElementById('doc-panel-content').innerHTML =
      `<div class="doc-panel-error">Could not load file: ${esc(String(e))}</div>`;
  }
}

function closeDocPanel() {
  document.querySelector('.app').classList.remove('doc-open');
  if (_activeDocBtn) {
    _activeDocBtn.classList.remove('dlc-opened');
    _activeDocBtn.textContent = 'Open';
    _activeDocBtn = null;
  }
}

// Resize handle drag
(function () {
  const handle = document.getElementById('doc-resize-handle');
  const panel  = document.getElementById('doc-panel');
  if (!handle || !panel) return;
  let dragging = false, startX = 0, startW = 0;
  handle.addEventListener('mousedown', e => {
    dragging = true;
    startX   = e.clientX;
    startW   = panel.offsetWidth;
    document.body.style.cursor     = 'col-resize';
    document.body.style.userSelect = 'none';
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const delta = startX - e.clientX;
    const newW  = Math.max(280, Math.min(startW + delta, window.innerWidth * 0.65));
    panel.style.width = newW + 'px';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor     = '';
    document.body.style.userSelect = '';
  });
})();

boot();
