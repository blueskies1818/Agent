import express from 'express';
import Anthropic from '@anthropic-ai/sdk';
import { fileURLToPath } from 'url';
import { dirname, join, resolve, relative, extname, basename } from 'path';
import { readFileSync, writeFileSync, unlinkSync, mkdirSync, readdirSync, statSync, existsSync, renameSync, rmSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();

app.use(express.json());
app.use(express.static(join(__dirname, 'public')));

const anthropic = new Anthropic();

// ── Config ───────────────────────────────────────────────────────────────
import { watch } from 'fs';
import { execSync } from 'child_process';

let VAULT_ROOT    = '';
let SCHEDULED_DIR = '';
try {
  // Read VAULT_DIR and SCHEDULED_DIR directly from the parent project's config.py
  const agentRoot = resolve(join(__dirname, '..'));
  const out = execSync(
    `python3 -c "import sys; sys.path.insert(0,'${agentRoot}'); from config import VAULT_DIR, SCHEDULED_DIR; import json; print(json.dumps({'vault': VAULT_DIR, 'scheduled': SCHEDULED_DIR}))"`,
    { encoding: 'utf8' }
  ).trim();
  const cfg = JSON.parse(out);
  VAULT_ROOT    = resolve(cfg.vault      || '');
  SCHEDULED_DIR = resolve(cfg.scheduled  || '');
} catch (e) {
  console.warn('  ⚠  Could not read config.py — vault/tasks API disabled.', e.message);
}

// ── Chat API — direct fallback (Glass Harness agent mode bypasses this) ──
app.post('/api/chat', async (req, res) => {
  const { messages } = req.body;

  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  try {
    const stream = anthropic.messages.stream({
      model: 'claude-sonnet-4-6',
      max_tokens: 8096,
      system:
        'You are Glass Harness, a helpful, creative, and intelligent assistant. ' +
        'Provide clear, thoughtful, and accurate responses. ' +
        'Use markdown formatting when appropriate — code blocks, lists, bold, etc.',
      messages,
    });

    stream.on('text', text => res.write(`data: ${JSON.stringify({ text })}\n\n`));
    stream.on('error', err => res.write(`data: ${JSON.stringify({ error: err.message })}\n\n`));

    await stream.finalMessage();
    res.write('data: [DONE]\n\n');
    res.end();
  } catch (err) {
    res.write(`data: ${JSON.stringify({ error: err.message })}\n\n`);
    res.end();
  }
});

// ── Vault helpers ─────────────────────────────────────────────────────────

function safePath(rel) {
  if (!VAULT_ROOT) return null;
  const abs = resolve(join(VAULT_ROOT, rel));
  // Prevent path traversal outside vault
  if (!abs.startsWith(VAULT_ROOT + '/') && abs !== VAULT_ROOT) return null;
  return abs;
}

const SKIP_DIRS = new Set(['.obsidian', '.git', 'sessions', 'node_modules']);

function buildTree(dir, vaultRoot) {
  let items = [];
  let entries;
  try { entries = readdirSync(dir); } catch { return []; }

  for (const name of entries.sort()) {
    if (name.startsWith('.')) continue;
    const abs  = join(dir, name);
    const rel  = relative(vaultRoot, abs);
    const stat = statSync(abs);
    if (stat.isDirectory()) {
      if (SKIP_DIRS.has(name)) continue;
      items.push({ type: 'dir', name, path: rel, children: buildTree(abs, vaultRoot) });
    } else if (extname(name).toLowerCase() === '.md') {
      items.push({ type: 'file', name, path: rel });
    }
  }
  return items;
}

function allMdFiles(dir, vaultRoot, out = []) {
  let entries;
  try { entries = readdirSync(dir); } catch { return out; }
  for (const name of entries) {
    if (name.startsWith('.')) continue;
    const abs  = join(dir, name);
    const stat = statSync(abs);
    if (stat.isDirectory()) {
      if (SKIP_DIRS.has(name)) continue;
      allMdFiles(abs, vaultRoot, out);
    } else if (extname(name).toLowerCase() === '.md') {
      out.push({ abs, rel: relative(vaultRoot, abs) });
    }
  }
  return out;
}

function parseWikilinks(content) {
  const re = /\[\[([^\]|#]+?)(?:[|#][^\]]*)?\]\]/g;
  const links = [];
  let m;
  while ((m = re.exec(content)) !== null) links.push(m[1].trim());
  return links;
}

// ── Vault API ─────────────────────────────────────────────────────────────

app.get('/api/vault/tree', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  res.json(buildTree(VAULT_ROOT, VAULT_ROOT));
});

app.get('/api/vault/file', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const abs = safePath(req.query.path || '');
  if (!abs || !existsSync(abs)) return res.status(404).json({ error: 'File not found' });
  try {
    res.json({ content: readFileSync(abs, 'utf8') });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/vault/file', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const { path: rel, content = '' } = req.body;
  const abs = safePath(rel || '');
  if (!abs) return res.status(400).json({ error: 'Invalid path' });
  try {
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, content, 'utf8');
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/vault/file/new', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const { path: rel } = req.body;
  const abs = safePath(rel || '');
  if (!abs) return res.status(400).json({ error: 'Invalid path' });
  if (existsSync(abs)) return res.status(409).json({ error: 'File already exists' });
  try {
    mkdirSync(dirname(abs), { recursive: true });
    writeFileSync(abs, `# ${basename(abs, '.md')}\n`, 'utf8');
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Index.json helpers ────────────────────────────────────────────────
function _readIndex() {
  const p = join(VAULT_ROOT, 'index.json');
  try { return existsSync(p) ? JSON.parse(readFileSync(p, 'utf8')) : { updated_at: '', buckets: {} }; }
  catch { return { updated_at: '', buckets: {} }; }
}
function _writeIndex(index) {
  index.updated_at = new Date().toISOString();
  writeFileSync(join(VAULT_ROOT, 'index.json'), JSON.stringify(index, null, 2), 'utf8');
}

app.post('/api/vault/folder', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const { path: rel, description = '', tags = [] } = req.body;
  const abs = safePath(rel || '');
  if (!abs) return res.status(400).json({ error: 'Invalid path' });
  if (existsSync(abs)) return res.status(409).json({ error: 'Folder already exists' });
  try {
    mkdirSync(abs, { recursive: true });
    // Register in index.json
    const index = _readIndex();
    const key   = basename(rel);
    index.buckets[key] = {
      path:          rel,
      description:   description,
      content_count: 0,
      tags:          Array.isArray(tags) ? tags : tags.split(',').map(t => t.trim()).filter(Boolean),
      created_at:    new Date().toISOString(),
    };
    _writeIndex(index);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.delete('/api/vault/folder', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const abs = safePath(req.query.path || '');
  if (!abs || !existsSync(abs)) return res.status(404).json({ error: 'Folder not found' });
  try {
    rmSync(abs, { recursive: true, force: true });
    // Remove matching bucket(s) from index.json
    const rel   = req.query.path;
    const index = _readIndex();
    let changed = false;
    for (const key of Object.keys(index.buckets)) {
      const b = index.buckets[key];
      if (b.path === rel || b.path.startsWith(rel + '/')) {
        delete index.buckets[key];
        changed = true;
      }
    }
    if (changed) _writeIndex(index);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/vault/move', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const { from: fromRel, to: toRel } = req.body;
  const fromAbs = safePath(fromRel || '');
  const toAbs   = safePath(toRel   || '');
  if (!fromAbs || !toAbs) return res.status(400).json({ error: 'Invalid path' });
  if (!existsSync(fromAbs)) return res.status(404).json({ error: 'Source not found' });
  if (existsSync(toAbs))   return res.status(409).json({ error: 'Destination already exists' });
  try {
    mkdirSync(dirname(toAbs), { recursive: true });
    renameSync(fromAbs, toAbs);
    // Update index.json bucket paths if a folder was moved
    if (statSync(toAbs).isDirectory()) {
      const index = _readIndex();
      let changed = false;
      for (const key of Object.keys(index.buckets)) {
        const b = index.buckets[key];
        if (b.path === fromRel || b.path.startsWith(fromRel + '/')) {
          b.path = toRel + b.path.slice(fromRel.length);
          changed = true;
        }
      }
      if (changed) _writeIndex(index);
    }
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.delete('/api/vault/file', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ error: 'Vault not configured' });
  const abs = safePath(req.query.path || '');
  if (!abs || !existsSync(abs)) return res.status(404).json({ error: 'File not found' });
  try {
    unlinkSync(abs);
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/vault/links', (req, res) => {
  if (!VAULT_ROOT) return res.status(503).json({ nodes: [], links: [] });

  const files  = allMdFiles(VAULT_ROOT, VAULT_ROOT);
  const byName = {};  // basename (no ext) → rel path
  files.forEach(f => {
    const key = basename(f.rel, '.md').toLowerCase();
    byName[key] = f.rel;
  });

  const degree = {};
  const rawLinks = [];

  files.forEach(f => {
    let content = '';
    try { content = readFileSync(f.abs, 'utf8'); } catch {}
    const wikilinks = parseWikilinks(content);
    wikilinks.forEach(rawLink => {
      // Obsidian resolves [[path/to/file]] by the basename — strip any path prefix
      const name   = rawLink.split('/').pop().toLowerCase();
      const target = byName[name];
      if (target && target !== f.rel) {
        rawLinks.push({ source: f.rel, target });
        degree[f.rel]  = (degree[f.rel]  || 0) + 1;
        degree[target] = (degree[target] || 0) + 1;
      }
    });
  });

  const nodes = files.map(f => ({
    id:     f.rel,
    label:  basename(f.rel, '.md'),
    degree: degree[f.rel] || 0,
  }));

  // Deduplicate links
  const seen  = new Set();
  const links = rawLinks.filter(l => {
    const k = [l.source, l.target].sort().join('||');
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });

  res.json({ nodes, links });
});

// ── Tasks API ─────────────────────────────────────────────────────────────

function safeTaskPath(filename) {
  if (!SCHEDULED_DIR) return null;
  // Only allow simple filenames (no path traversal)
  if (!filename || filename.includes('/') || filename.includes('..')) return null;
  if (!filename.endsWith('.json')) return null;
  return join(SCHEDULED_DIR, filename);
}

app.get('/api/tasks', (req, res) => {
  if (!SCHEDULED_DIR) return res.json([]);
  try {
    const files = readdirSync(SCHEDULED_DIR)
      .filter(f => f.endsWith('.json') && !f.endsWith('.json.example'));
    const tasks = files.map(f => {
      try {
        const data = JSON.parse(readFileSync(join(SCHEDULED_DIR, f), 'utf8'));
        return { ...data, _filename: f };
      } catch { return null; }
    }).filter(Boolean);
    res.json(tasks);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/tasks', (req, res) => {
  if (!SCHEDULED_DIR) return res.status(503).json({ error: 'Tasks not configured' });
  const task = req.body;
  if (!task.task_id) return res.status(400).json({ error: 'task_id required' });
  const filename = `${task.task_id}.json`;
  const abs      = safeTaskPath(filename);
  if (!abs) return res.status(400).json({ error: 'Invalid task_id' });
  try {
    writeFileSync(abs, JSON.stringify(task, null, 2), 'utf8');
    res.json({ ok: true, filename });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.put('/api/tasks/:filename', (req, res) => {
  if (!SCHEDULED_DIR) return res.status(503).json({ error: 'Tasks not configured' });
  const abs = safeTaskPath(req.params.filename);
  if (!abs || !existsSync(abs)) return res.status(404).json({ error: 'Task not found' });
  try {
    const existing = JSON.parse(readFileSync(abs, 'utf8'));
    const updated  = { ...existing, ...req.body, _filename: undefined };
    delete updated._filename;
    writeFileSync(abs, JSON.stringify(updated, null, 2), 'utf8');
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.delete('/api/tasks/:filename', (req, res) => {
  if (!SCHEDULED_DIR) return res.status(503).json({ error: 'Tasks not configured' });
  const abs = safeTaskPath(req.params.filename);
  if (!abs || !existsSync(abs)) return res.status(404).json({ error: 'Task not found' });
  try { unlinkSync(abs); res.json({ ok: true }); }
  catch (e) { res.status(500).json({ error: e.message }); }
});

// SSE — streams a message whenever a .json file changes in the scheduled dir
app.get('/api/tasks/watch', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const send = () => res.write(`data: refresh\n\n`);

  let watcher = null;
  if (SCHEDULED_DIR && existsSync(SCHEDULED_DIR)) {
    try {
      watcher = watch(SCHEDULED_DIR, (event, filename) => {
        if (filename && filename.endsWith('.json') && !filename.endsWith('.json.example')) {
          send();
        }
      });
    } catch {}
  }

  // Keepalive ping every 25 s so the connection doesn't timeout
  const ping = setInterval(() => res.write(': ping\n\n'), 25000);
  req.on('close', () => { clearInterval(ping); watcher?.close(); });
});

// ── Workspace file reader (for doc-link widgets) ─────────────────────────
// Restricted to files inside the workspace directory.
const WORKSPACE_ROOT = VAULT_ROOT ? resolve(join(VAULT_ROOT, '..')) : '';

app.get('/api/workspace/file', (req, res) => {
  if (!WORKSPACE_ROOT) return res.status(503).json({ error: 'Workspace not configured' });
  const raw = req.query.path || '';
  if (!raw) return res.status(400).json({ error: 'path required' });
  const abs = resolve(raw);
  if (!abs.startsWith(WORKSPACE_ROOT + '/') && abs !== WORKSPACE_ROOT) {
    return res.status(403).json({ error: 'Path outside workspace' });
  }
  try {
    const stat    = statSync(abs);
    const content = readFileSync(abs, 'utf8');
    res.json({
      name:        basename(abs),
      path:        abs,
      content,
      modified_at: stat.mtime.toISOString(),
    });
  } catch (e) {
    res.status(404).json({ error: 'File not found: ' + e.message });
  }
});

// ── Agent proxy ───────────────────────────────────────────────────────────
// Forwards requests to the Python agent's FastAPI server so the browser never
// needs to cross origins.  AGENT_URL defaults to the agent's standard port.
const AGENT_BASE = process.env.AGENT_URL || 'http://127.0.0.1:8765';

app.post('/api/agent/queue', async (req, res) => {
  try {
    const r = await fetch(`${AGENT_BASE}/queue`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'Agent offline: ' + e.message });
  }
});

app.delete('/api/agent/tasks/:task_id', async (req, res) => {
  try {
    const r = await fetch(`${AGENT_BASE}/tasks/${req.params.task_id}`, { method: 'DELETE' });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'Agent offline: ' + e.message });
  }
});

// SSE passthrough — streams the agent's typed event chunks directly to the browser
app.get('/api/agent/stream/:task_id', async (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();
  try {
    const r = await fetch(`${AGENT_BASE}/stream/${req.params.task_id}`);
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    req.on('close', () => reader.cancel().catch(() => {}));
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(decoder.decode(value, { stream: true }));
    }
  } catch (e) {
    res.write(`data: ${JSON.stringify({ type: 'error', content: 'Stream error: ' + e.message })}\n\n`);
  }
  res.end();
});

// ── Conversation session proxy → Glass Harness ───────────────────────────────
// Glass AI persists and indexes conversations by calling these through to the
// Glass Harness FastAPI server.  The Node layer is just a thin proxy.

app.get('/api/conversations', async (req, res) => {
  try {
    const r = await fetch(`${AGENT_BASE}/conversations`);
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'Glass Harness offline: ' + e.message });
  }
});

app.post('/api/conversations/:cid', async (req, res) => {
  try {
    const r = await fetch(`${AGENT_BASE}/conversations/${req.params.cid}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'Glass Harness offline: ' + e.message });
  }
});

app.post('/api/conversations/:cid/reindex', async (req, res) => {
  try {
    const r = await fetch(`${AGENT_BASE}/conversations/${req.params.cid}/reindex`, {
      method: 'POST',
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'Glass Harness offline: ' + e.message });
  }
});

app.delete('/api/conversations/:cid', async (req, res) => {
  try {
    const r = await fetch(`${AGENT_BASE}/conversations/${req.params.cid}`, {
      method: 'DELETE',
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'Glass Harness offline: ' + e.message });
  }
});

// ── Start ─────────────────────────────────────────────────────────────────
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  const hasKey   = !!process.env.ANTHROPIC_API_KEY;
  const hasVault = !!VAULT_ROOT;
  console.log(`\n  ✦ Glass AI  →  http://localhost:${PORT}`);
  if (!hasKey)   console.log('  ⚠  ANTHROPIC_API_KEY not set — chat runs in demo mode');
  else           console.log('  ✓  API key detected');
  if (hasVault)        console.log(`  ✓  Vault: ${VAULT_ROOT}`);
  if (SCHEDULED_DIR)   console.log(`  ✓  Tasks: ${SCHEDULED_DIR}`);
  else           console.log('  ⚠  No vault configured');
  console.log('');
});
