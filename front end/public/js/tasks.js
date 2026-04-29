/* ─── Recurring Tasks module ────────────────────────────────────────────── */

let tasksMode    = false;
let _tasks       = [];
let _taskWatch   = null;  // EventSource for live reload
let _editingTask = null;  // filename of task being edited

/* ─── Mode switching ─────────────────────────────────────────────────── */
function enterTasksMode() {
  // Exit KB mode first — exitKBMode restores chat-main visibility,
  // which we override below. Must happen before we set display states.
  if (typeof exitKBMode === 'function' && typeof vaultMode !== 'undefined' && vaultMode) {
    exitKBMode();
  }

  tasksMode = true;
  document.getElementById('chat-main').style.display       = 'none';
  document.getElementById('kb-main').style.display         = 'none';
  document.getElementById('tasks-main').style.display      = 'flex';
  document.getElementById('history-wrap').style.display    = 'none';
  document.getElementById('kb-tree-wrap').style.display    = 'none';
  document.getElementById('tasks-side-wrap').style.display = 'flex';
  document.getElementById('stab-chat').classList.remove('active');
  document.getElementById('stab-kb').classList.remove('active');
  document.getElementById('stab-tasks').classList.add('active');
  loadTasks();
  startTaskWatch();
}

function exitTasksMode() {
  tasksMode = false;
  stopTaskWatch();
  document.getElementById('tasks-main').style.display      = 'none';
  document.getElementById('tasks-side-wrap').style.display = 'none';
  document.getElementById('stab-tasks').classList.remove('active');
}

// Called by the shared setSidebarTab dispatcher in app.js
function _enterTasksModeFromTab() {
  // Deactivate other modes first
  if (typeof vaultMode !== 'undefined' && vaultMode) {
    document.getElementById('kb-main').style.display        = 'none';
    document.getElementById('kb-tree-wrap').style.display   = 'none';
    if (typeof graph !== 'undefined' && graph) graph.stop();
  }
  enterTasksMode();
}

/* ─── Live reload via SSE ────────────────────────────────────────────── */
function startTaskWatch() {
  if (_taskWatch) return;
  _taskWatch = new EventSource('/api/tasks/watch');
  _taskWatch.onmessage = () => loadTasks();
  _taskWatch.onerror   = () => { /* silently retry */ };
}

function stopTaskWatch() {
  if (_taskWatch) { _taskWatch.close(); _taskWatch = null; }
}

/* ─── Load & render ──────────────────────────────────────────────────── */
async function loadTasks() {
  try {
    const res = await fetch('/api/tasks');
    _tasks    = await res.json();
    renderSideList(_tasks);
    renderTaskGrid(_tasks);
  } catch (e) { console.error('Load tasks failed', e); }
}

function renderSideList(tasks) {
  const el = document.getElementById('tasks-side-list');
  el.innerHTML = '';
  if (!tasks.length) {
    el.innerHTML = '<div style="padding:8px 6px;font-size:12px;color:var(--text-dim)">No tasks yet</div>';
    return;
  }
  tasks.forEach(t => {
    const status = taskStatus(t);
    const row = document.createElement('div');
    row.className = 'hist-item task-side-item';
    row.innerHTML =
      `<span class="task-status-dot status-${status}"></span>` +
      `<span class="hist-title">${esc(taskDisplayName(t))}</span>`;
    row.onclick = () => showTaskDetail(t);
    el.appendChild(row);
  });
}

function renderTaskGrid(tasks) {
  const grid = document.getElementById('tasks-grid');
  grid.innerHTML = '';
  if (!tasks.length) {
    grid.innerHTML =
      `<div class="tasks-empty">` +
      `<div class="tasks-empty-icon">⏱</div>` +
      `<div class="tasks-empty-text">No recurring tasks yet</div>` +
      `<button class="chip" onclick="showNewTaskForm()">Create your first task</button>` +
      `</div>`;
    return;
  }
  tasks.forEach(t => {
    grid.appendChild(makeTaskCard(t));
  });
}

function makeTaskCard(t) {
  const status = taskStatus(t);
  const card   = document.createElement('div');
  card.className = 'task-card glass';
  card.innerHTML = `
    <div class="task-card-top">
      <span class="task-status-dot status-${status}" title="${status}"></span>
      <span class="task-name">${esc(taskDisplayName(t))}</span>
      <span class="task-schedule-pill">${esc(fmtSchedule(t.schedule))}</span>
    </div>
    <div class="task-prompt">${esc(t.prompt || '')}</div>
    <div class="task-card-foot">
      <span class="task-meta">Next: <b>${relTime(t.next_run)}</b></span>
      <span class="task-meta">Last: <b>${relTime(t.last_run)}</b></span>
      <div class="task-card-actions">
        <button class="task-action-btn" onclick="showTaskDetail(${JSON.stringify(t).replace(/"/g,'&quot;')})">Edit</button>
        <button class="task-action-btn danger" onclick="deleteTask('${esc(t._filename)}',event)">Delete</button>
      </div>
    </div>`;
  card.onclick = (e) => {
    if (e.target.closest('.task-action-btn')) return;
    showTaskDetail(t);
  };
  return card;
}

/* ─── Task detail / edit view ────────────────────────────────────────── */
function showTaskDetail(t) {
  _editingTask = t._filename || null;
  document.getElementById('tasks-main').style.display        = 'flex';
  document.getElementById('tasks-grid-view').style.display   = 'none';
  document.getElementById('tasks-detail-view').style.display = 'flex';

  document.getElementById('td-title').textContent = _editingTask ? taskDisplayName(t) : 'New Task';
  document.getElementById('td-prompt').value        = t.prompt        || '';
  document.getElementById('td-sched-type').value    = t.schedule?.type  || 'interval';
  document.getElementById('td-sched-val').value     = t.schedule?.value || '';
  document.getElementById('td-priority').value      = t.priority      ?? 1;
  document.getElementById('td-next-run').value      = t.next_run
    ? new Date(t.next_run).toISOString().slice(0,16) : '';
  // Termination
  const termType = t.termination?.type || 'never';
  document.getElementById('td-term-type').value = termType;
  document.getElementById('td-term-date').value = t.termination?.date
    ? new Date(t.termination.date).toISOString().slice(0,16) : '';
  document.getElementById('td-term-count').value = t.termination?.count || '';
  _updateTermFields();
}

function showNewTaskForm() {
  // Ensure tasks panel is in the right state regardless of how we got here
  document.getElementById('tasks-main').style.display       = 'flex';
  document.getElementById('tasks-grid-view').style.display  = 'none';
  document.getElementById('tasks-detail-view').style.display = 'flex';

  _editingTask = null;
  document.getElementById('td-title').textContent        = 'New Task';
  document.getElementById('td-prompt').value             = '';
  document.getElementById('td-sched-type').value         = 'interval';
  document.getElementById('td-sched-val').value          = '';
  document.getElementById('td-priority').value           = '1';
  document.getElementById('td-next-run').value           = '';
  document.getElementById('td-term-type').value          = 'never';
  document.getElementById('td-term-date').value          = '';
  document.getElementById('td-term-count').value         = '';
  _updateTermFields();
  document.getElementById('td-prompt').focus();
}

function closeTaskDetail() {
  document.getElementById('tasks-detail-view').style.display = 'none';
  document.getElementById('tasks-grid-view').style.display   = 'flex';
  _editingTask = null;
}

function _updateTermFields() {
  const type  = document.getElementById('td-term-type').value;
  document.getElementById('td-term-date-wrap').style.display  = type === 'on_date'     ? '' : 'none';
  document.getElementById('td-term-count-wrap').style.display = type === 'after_runs'  ? '' : 'none';
}

async function saveTaskDetail() {
  const prompt    = document.getElementById('td-prompt').value.trim();
  if (!prompt) { document.getElementById('td-prompt').focus(); return; }

  const schedType = document.getElementById('td-sched-type').value;
  const schedVal  = document.getElementById('td-sched-val').value.trim();
  const nextRunRaw = document.getElementById('td-next-run').value;
  const termType  = document.getElementById('td-term-type').value;

  const task = {
    prompt,
    schedule:  { type: schedType, value: schedVal || undefined },
    priority:  parseInt(document.getElementById('td-priority').value) || 1,
    next_run:  nextRunRaw ? new Date(nextRunRaw).toISOString() : new Date().toISOString(),
    last_run:  null,
    pending_task_id: null,
    session:   'new',
    skills:    [],
    created_at: new Date().toISOString(),
    termination: _buildTermination(termType),
  };

  try {
    if (_editingTask) {
      await fetch(`/api/tasks/${encodeURIComponent(_editingTask)}`, {
        method:  'PUT',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(task),
      });
    } else {
      // Generate task_id from date + slug of prompt
      const slug = prompt.toLowerCase().replace(/[^a-z0-9]+/g, '-').slice(0, 32).replace(/-$/, '');
      const date = new Date().toISOString().slice(0, 10);
      task.task_id = `${date}_${slug}`;
      await fetch('/api/tasks', {
        method:  'POST',
        headers: { 'Content-Type': 'application/json' },
        body:    JSON.stringify(task),
      });
    }
    closeTaskDetail();
    await loadTasks();
  } catch (e) { console.error('Save task failed', e); }
}

function _buildTermination(type) {
  if (type === 'on_date') {
    const d = document.getElementById('td-term-date').value;
    return { type, date: d ? new Date(d).toISOString() : null };
  }
  if (type === 'after_runs') {
    return { type, count: parseInt(document.getElementById('td-term-count').value) || 1 };
  }
  return { type: 'never' };
}

async function deleteTask(filename, e) {
  if (e) e.stopPropagation();
  if (!confirm('Delete this task?')) return;
  try {
    await fetch(`/api/tasks/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    await loadTasks();
  } catch (e) { console.error('Delete task failed', e); }
}

/* ─── Helpers ────────────────────────────────────────────────────────── */
function taskDisplayName(t) {
  const id = t.task_id || t._filename?.replace(/\.json$/, '') || 'Task';
  // Strip leading date prefix like "2026-05-01_"
  return id.replace(/^\d{4}-\d{2}-\d{2}_/, '').replace(/-/g, ' ');
}

function taskStatus(t) {
  if (t.pending_task_id)                         return 'running';
  if (!t.next_run)                               return 'complete';
  if (new Date(t.next_run) <= Date.now())        return 'pending';
  return 'scheduled';
}

function fmtSchedule(sch) {
  if (!sch) return '—';
  switch (sch.type) {
    case 'interval': return `Every ${sch.value || '?'}`;
    case 'cron':     return `Cron`;
    case 'once':     return 'One-time';
    case 'daily':    return `Daily ${sch.value || ''}`;
    default:         return sch.type || '—';
  }
}

function relTime(iso) {
  if (!iso) return 'never';
  const diff = new Date(iso) - Date.now();
  const abs  = Math.abs(diff);
  const mins = Math.floor(abs / 60000);
  const hrs  = Math.floor(abs / 3600000);
  const days = Math.floor(abs / 86400000);
  const pre  = diff > 0 ? 'in ' : '';
  const suf  = diff < 0 ? ' ago' : '';
  if (mins < 1)   return diff > 0 ? 'soon' : 'just now';
  if (mins < 60)  return `${pre}${mins}m${suf}`;
  if (hrs  < 24)  return `${pre}${hrs}h${suf}`;
  if (days < 30)  return `${pre}${days}d${suf}`;
  return new Date(iso).toLocaleDateString();
}

document.addEventListener('DOMContentLoaded', () => {
  const termSel = document.getElementById('td-term-type');
  if (termSel) termSel.addEventListener('change', _updateTermFields);
});
