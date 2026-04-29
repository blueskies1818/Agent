/* ─── Vault / Knowledge-Base module ────────────────────────────────────── */

let vaultMode       = false;
let currentFilePath = null;
let editorDirty     = false;
let graph           = null;
let _vaultFolders   = ['']; // cached folder list for modal
let _zoomStack      = [];   // stack of folder paths for zoom back-navigation
let _zoomCurrent    = null; // currently zoomed folder path (null = all)

/* ─── Mode switching ─────────────────────────────────────────────────── */
function enterKBMode() {
  vaultMode = true;
  document.getElementById('chat-main').style.display    = 'none';
  document.getElementById('kb-main').style.display      = 'flex';
  document.getElementById('history-wrap').style.display = 'none';
  document.getElementById('kb-tree-wrap').style.display = 'flex';
  document.getElementById('stab-chat').classList.remove('active');
  document.getElementById('stab-kb').classList.add('active');
  initGraph();
  loadVaultTree();
}

function exitKBMode() {
  vaultMode = false;
  if (graph) graph.stop();
  document.getElementById('chat-main').style.display    = '';
  document.getElementById('kb-main').style.display      = 'none';
  document.getElementById('history-wrap').style.display = '';
  document.getElementById('kb-tree-wrap').style.display = 'none';
  document.getElementById('stab-chat').classList.add('active');
  document.getElementById('stab-kb').classList.remove('active');
}

function _setSidebarTabVault(tab) {
  if (tab === 'kb') enterKBMode();
  else              exitKBMode();
}

/* ─── Graph init ─────────────────────────────────────────────────────── */
function initGraph() {
  const canvas = document.getElementById('kb-canvas');
  if (!graph) {
    graph = new ForceGraph(
      canvas,
      node => openVaultFile(node.id),          // onOpen
      node => _onNodeSelected(node),           // onSelect
    );
    graph.onConnect = (src, tgt) => connectNodes(src, tgt);
  }
  _resizeCanvas();
  loadGraphData();

  if (!graph._resizeObs) {
    graph._resizeObs = new ResizeObserver(() => _resizeCanvas());
    graph._resizeObs.observe(document.getElementById('kb-graph-pane'));
  }
}

function _resizeCanvas() {
  const pane = document.getElementById('kb-graph-pane');
  const rect = pane.getBoundingClientRect();
  if (rect.width && rect.height) graph.resize(rect.width, rect.height);
}

async function loadGraphData() {
  try {
    const res  = await fetch('/api/vault/links');
    const data = await res.json();
    _applyZoom(data.nodes, data.links);
  } catch (e) { console.error('Graph load failed', e); }
}

/* ─── Folder zoom ─────────────────────────────────────────────────────── */
function _applyZoom(nodes, links) {
  if (!_zoomCurrent) {
    graph.setData(nodes, links);
    _renderTreeForZoom();
    _updateZoomNav();
    return;
  }
  // Strictly only nodes inside this folder (no external neighbours)
  const prefix        = _zoomCurrent.endsWith('/') ? _zoomCurrent : _zoomCurrent + '/';
  const kept          = new Set(nodes.filter(n => n.id.startsWith(prefix)).map(n => n.id));
  const filteredNodes = nodes.filter(n => kept.has(n.id));
  const filteredLinks = links.filter(l => kept.has(l.source) && kept.has(l.target));
  graph.setData(filteredNodes, filteredLinks);
  _renderTreeForZoom();
  _updateZoomNav();
}

function zoomToFolder(folderPath) {
  _zoomStack.push(_zoomCurrent);
  _zoomCurrent = folderPath;
  loadGraphData();
  _updateZoomNav();
}

function zoomHome() {
  _zoomStack = [];
  _zoomCurrent = null;
  loadGraphData();
  _updateZoomNav();
}

function zoomBack() {
  if (!_zoomStack.length) return;
  _zoomCurrent = _zoomStack.pop();
  loadGraphData();
  _updateZoomNav();
}


function _updateZoomNav() {
  const wrap  = document.getElementById('vault-zoom-nav');
  const label = document.getElementById('zoom-nav-label');
  if (!wrap) return;
  const active = _zoomCurrent !== null || _zoomStack.length > 0;
  wrap.style.display = active ? 'flex' : 'none';
  if (label) label.textContent = _zoomCurrent ? _zoomCurrent.split('/').pop() : 'All files';
}

/* ─── Context menu (right-click folder) ─────────────────────────────── */
let _ctxMenu = null;
function _showFolderCtxMenu(e, folderPath) {
  e.preventDefault();
  _dismissCtxMenu();
  const menu = document.createElement('div');
  menu.className  = 'tree-ctx-menu glass';
  menu.innerHTML  = `<button class="tree-ctx-item" onclick="zoomToFolder(${JSON.stringify(folderPath).replace(/"/g,'&quot;')});_dismissCtxMenu()">🔍 Zoom to folder</button>`;
  menu.style.cssText = `position:fixed;top:${e.clientY}px;left:${e.clientX}px;z-index:1000;`;
  document.body.appendChild(menu);
  _ctxMenu = menu;
  setTimeout(() => document.addEventListener('click', _dismissCtxMenu, { once: true }), 0);
}
function _dismissCtxMenu() {
  if (_ctxMenu) { _ctxMenu.remove(); _ctxMenu = null; }
}

/* ─── Graph mode toolbar ─────────────────────────────────────────────── */
function setGraphMode(mode) {
  graph.setMode(mode);
  document.getElementById('kg-nav').classList.toggle('active',     mode === 'navigate');
  document.getElementById('kg-connect').classList.toggle('active', mode === 'connect');

  const pill = document.getElementById('kb-hint-pill');
  if (mode === 'connect') {
    pill.textContent = 'Connect: click source node, then target node · Esc to cancel';
  } else {
    pill.textContent = 'Click to select · Click again to open · Drag to pin · Dbl-click to unpin';
  }
}

function _onNodeSelected(_node) {
  // node selection used for connect-mode highlight only; delete is in tree items
}

/* ─── Connect / disconnect nodes ─────────────────────────────────────── */
async function connectNodes(source, target) {
  const alreadyLinked = graph.links.some(l =>
    (l.source.id === source.id && l.target.id === target.id) ||
    (l.source.id === target.id && l.target.id === source.id)
  );

  if (alreadyLinked) {
    await _removeLink(source, target);
    return;
  }

  try {
    const res  = await fetch(`/api/vault/file?path=${encodeURIComponent(source.id)}`);
    const data = await res.json();
    if (data.content == null) { setGraphMode('navigate'); return; }
    const link = `[[${target.label}]]`;
    if (data.content.includes(link)) { setGraphMode('navigate'); return; }

    const newContent = data.content.trimEnd() + '\n\n' + link + '\n';
    await fetch('/api/vault/file', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: source.id, content: newContent }),
    });
    await loadGraphData();
    setGraphMode('navigate');
    graph.selectNode(graph.nodes.find(n => n.id === source.id) || null);
  } catch (e) { console.error('Connect failed', e); }
}

async function _removeLink(source, target) {
  // Remove wikilink in whichever file holds it (check both directions)
  const pairs = [
    { file: source.id, link: `[[${target.label}]]` },
    { file: target.id, link: `[[${source.label}]]` },
  ];
  try {
    for (const { file, link } of pairs) {
      const res  = await fetch(`/api/vault/file?path=${encodeURIComponent(file)}`);
      const data = await res.json();
      if (data.content && data.content.includes(link)) {
        const cleaned = data.content
          .split('\n')
          .filter(line => !line.includes(link))
          .join('\n')
          .replace(/\n{3,}/g, '\n\n')
          .trim() + '\n';
        await fetch('/api/vault/file', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: file, content: cleaned }),
        });
      }
    }
    await loadGraphData();
    setGraphMode('navigate');
  } catch (e) { console.error('Disconnect failed', e); }
}

/* ─── Delete vault file ──────────────────────────────────────────────── */
async function deleteVaultFile(path, e) {
  e.stopPropagation();
  const name = path.split('/').pop().replace(/\.md$/i, '');
  if (!confirm(`Delete "${name}.md"? This cannot be undone.`)) return;
  try {
    await fetch(`/api/vault/file?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
    if (graph && graph.selectedNode && graph.selectedNode.id === path) {
      graph.selectNode(null);
    }
    if (currentFilePath === path) closeEditor();
    await loadVaultTree();
    await loadGraphData();
  } catch (e) { console.error('Delete failed', e); }
}

async function deleteVaultFolder(path, e) {
  e.stopPropagation();
  const name = path.split('/').pop();
  if (!confirm(`Delete folder "${name}" and all its contents? This cannot be undone.`)) return;
  try {
    await fetch(`/api/vault/folder?path=${encodeURIComponent(path)}`, { method: 'DELETE' });
    if (currentFilePath && currentFilePath.startsWith(path + '/')) closeEditor();
    await loadVaultTree();
    await loadGraphData();
  } catch (e) { console.error('Delete folder failed', e); }
}

/* ─── Add file modal ─────────────────────────────────────────────────── */
async function showAddFileModal() {
  // Populate folder dropdown from vault tree
  try {
    const res  = await fetch('/api/vault/tree');
    const tree = await res.json();
    _vaultFolders = [''];
    _collectFolders(tree, _vaultFolders);
    const sel = document.getElementById('kg-modal-folder');
    sel.innerHTML = '<option value="">/ vault root</option>';
    _vaultFolders.slice(1).forEach(f => {
      const opt = document.createElement('option');
      opt.value = f; opt.textContent = f + '/';
      sel.appendChild(opt);
    });
  } catch {}

  document.getElementById('kg-modal-name').value = '';
  document.getElementById('kg-modal').style.display = 'flex';
  document.getElementById('kg-modal-name').focus();
  _setModalType('file');
}

let _modalType = 'file'; // 'file' | 'folder'

function _setModalType(type) {
  _modalType = type;
  const isFolder = type === 'folder';
  document.getElementById('kg-type-file').classList.toggle('active',   !isFolder);
  document.getElementById('kg-type-folder').classList.toggle('active',  isFolder);
  document.getElementById('kg-modal-name-label').textContent    = isFolder ? 'Folder name' : 'File name';
  document.getElementById('kg-modal-name').placeholder          = isFolder ? 'my-folder'   : 'my-note';
  document.getElementById('kg-modal-desc-label').style.display  = isFolder ? '' : 'none';
  document.getElementById('kg-modal-desc').style.display        = isFolder ? '' : 'none';
  document.getElementById('kg-modal-tags-label').style.display  = isFolder ? '' : 'none';
  document.getElementById('kg-modal-tags').style.display        = isFolder ? '' : 'none';
  if (!isFolder) {
    document.getElementById('kg-modal-desc').value = '';
    document.getElementById('kg-modal-tags').value = '';
  }
  _updateModalPreview();
}

function _collectFolders(items, out) {
  items.forEach(item => {
    if (item.type === 'dir') {
      out.push(item.path);
      _collectFolders(item.children || [], out);
    }
  });
}

function hideAddFileModal(e) {
  if (e && e.target !== document.getElementById('kg-modal')) return;
  document.getElementById('kg-modal').style.display = 'none';
}

function _updateModalPreview() {
  const raw    = (document.getElementById('kg-modal-name').value || (_modalType === 'file' ? 'my-note' : 'my-folder')).trim();
  const name   = _modalType === 'file' ? raw.replace(/\.md$/i, '') : raw;
  const folder = document.getElementById('kg-modal-folder').value;
  const path   = folder ? `${folder}/${name}` : name;
  document.getElementById('kg-modal-preview').textContent = _modalType === 'file' ? path + '.md' : path + '/';
}

async function createFromModal() {
  const raw    = (document.getElementById('kg-modal-name').value || '').trim();
  const name   = _modalType === 'file' ? raw.replace(/\.md$/i, '') : raw;
  if (!name) { document.getElementById('kg-modal-name').focus(); return; }
  const folder = document.getElementById('kg-modal-folder').value;

  if (_modalType === 'folder') {
    const path        = folder ? `${folder}/${name}` : name;
    const description = document.getElementById('kg-modal-desc').value.trim();
    const tagsRaw     = document.getElementById('kg-modal-tags').value;
    const tags        = tagsRaw.split(',').map(t => t.trim()).filter(Boolean);
    try {
      const res  = await fetch('/api/vault/folder', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, description, tags }),
      });
      const data = await res.json();
      if (data.error) { alert(data.error); return; }
      document.getElementById('kg-modal').style.display = 'none';
      await loadVaultTree();
    } catch (e) { console.error('Create folder failed', e); }
    return;
  }

  // File
  const path = folder ? `${folder}/${name}.md` : `${name}.md`;
  try {
    const res  = await fetch('/api/vault/file/new', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    document.getElementById('kg-modal').style.display = 'none';
    await loadVaultTree();
    await loadGraphData();
    openVaultFile(path);
  } catch (e) { console.error('Create file failed', e); }
}

/* ─── Move (drag-and-drop) ───────────────────────────────────────────── */
async function _moveItem(fromPath, toFolder) {
  const name    = fromPath.split('/').pop();
  const newPath = toFolder ? `${toFolder}/${name}` : name;
  if (newPath === fromPath) return;
  // Prevent dropping a folder into its own subtree
  if (toFolder && (toFolder === fromPath || toFolder.startsWith(fromPath + '/'))) return;
  try {
    const res  = await fetch('/api/vault/move', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from: fromPath, to: newPath }),
    });
    const data = await res.json();
    if (data.error) { console.error('Move failed:', data.error); return; }
    if (currentFilePath === fromPath) currentFilePath = newPath;
    await loadVaultTree();
    await loadGraphData();
  } catch (e) { console.error('Move failed', e); }
}

/* ─── File tree ──────────────────────────────────────────────────────── */
let _fullTree = []; // cached full vault tree

async function loadVaultTree() {
  try {
    const res  = await fetch('/api/vault/tree');
    _fullTree  = await res.json();
    _renderTreeForZoom();
  } catch (e) { console.error('Tree load failed', e); }
}

/* Find the subtree node whose path matches folderPath, searching recursively */
function _findTreeNode(items, folderPath) {
  for (const item of items) {
    if (item.type === 'dir') {
      if (item.path === folderPath) return item;
      const found = _findTreeNode(item.children || [], folderPath);
      if (found) return found;
    }
  }
  return null;
}

/* Render the tree filtered to the current zoom level */
function _renderTreeForZoom() {
  const el = document.getElementById('kb-tree-list');
  el.innerHTML = '';

  // Root drop zone — drop here to move item to vault root (or zoomed folder root)
  el.addEventListener('dragover', e => { if (e.target === el) { e.preventDefault(); el.classList.add('drag-over-root'); } });
  el.addEventListener('dragleave', e => { if (e.target === el) el.classList.remove('drag-over-root'); });
  el.addEventListener('drop', e => {
    if (e.target !== el) return;
    e.preventDefault();
    el.classList.remove('drag-over-root');
    try {
      const d = JSON.parse(e.dataTransfer.getData('text/plain'));
      _moveItem(d.path, _zoomCurrent || '');
    } catch {}
  });

  if (!_zoomCurrent) {
    renderTree(_fullTree, el, 0);
    return;
  }
  const node = _findTreeNode(_fullTree, _zoomCurrent);
  renderTree(node ? (node.children || []) : _fullTree, el, 0);
}

function renderTree(items, container, depth) {
  items.forEach(item => {
    const row = document.createElement('div');
    row.className = 'tree-item';
    row.style.paddingLeft = (8 + depth * 14) + 'px';
    row.draggable = true;
    row.dataset.dragPath = item.path;

    row.addEventListener('dragstart', e => {
      e.stopPropagation();
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', JSON.stringify({ path: item.path, type: item.type }));
      setTimeout(() => row.classList.add('tree-dragging'), 0);
    });
    row.addEventListener('dragend', () => row.classList.remove('tree-dragging'));

    if (item.type === 'dir') {
      row.classList.add('tree-dir');
      row.innerHTML =
        `<span class="tree-caret">▾</span>` +
        `<span class="tree-icon">📁</span>` +
        `<span class="tree-name">${esc(item.name)}</span>` +
        `<button class="tree-del" title="Delete folder" onclick="deleteVaultFolder(${JSON.stringify(item.path).replace(/"/g,'&quot;')},event)">` +
          `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
            `<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M9 6V4h6v2"/>` +
          `</svg>` +
        `</button>`;
      const kids = document.createElement('div');
      kids.className = 'tree-children';
      container.appendChild(row);
      container.appendChild(kids);
      renderTree(item.children || [], kids, depth + 1);

      row.addEventListener('click', () => {
        const open = !kids.classList.contains('collapsed');
        kids.classList.toggle('collapsed', open);
        row.querySelector('.tree-caret').textContent = open ? '▸' : '▾';
      });
      row.addEventListener('contextmenu', e => _showFolderCtxMenu(e, item.path));

      // Drop target — accept files and folders dragged onto this folder
      row.addEventListener('dragover', e => { e.preventDefault(); e.stopPropagation(); row.classList.add('tree-drop-over'); });
      row.addEventListener('dragleave', () => row.classList.remove('tree-drop-over'));
      row.addEventListener('drop', e => {
        e.preventDefault(); e.stopPropagation();
        row.classList.remove('tree-drop-over');
        try {
          const d = JSON.parse(e.dataTransfer.getData('text/plain'));
          _moveItem(d.path, item.path);
        } catch {}
      });
    } else {
      const label = item.name.replace(/\.md$/i, '');
      row.dataset.path = item.path;
      row.innerHTML =
        `<span class="tree-icon file-icon">◆</span>` +
        `<span class="tree-name">${esc(label)}</span>` +
        `<button class="tree-del" title="Delete" onclick="deleteVaultFile(${JSON.stringify(item.path).replace(/"/g,'&quot;')},event)">` +
          `<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
            `<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M9 6V4h6v2"/>` +
          `</svg>` +
        `</button>`;
      row.addEventListener('click', () => openVaultFile(item.path));
      container.appendChild(row);
    }
  });
}

/* ─── Open / close file ──────────────────────────────────────────────── */
async function openVaultFile(path) {
  try {
    const res  = await fetch(`/api/vault/file?path=${encodeURIComponent(path)}`);
    const data = await res.json();

    currentFilePath = path;
    editorDirty     = false;

    const name = path.split('/').pop().replace(/\.md$/i, '');
    document.getElementById('kb-editor-name').textContent = name;
    document.getElementById('kb-save-btn').textContent    = 'Save';

    const editor = document.getElementById('kb-editor');
    editor.innerHTML = mdHTML(data.content);
    editor.querySelectorAll('pre code:not(.hljs)').forEach(el => {
      try { hljs.highlightElement(el); } catch {}
    });

    document.getElementById('kb-graph-view').style.display = 'none';
    document.getElementById('kb-file-view').style.display  = 'flex';
    editor.focus();

    document.querySelectorAll('.tree-item[data-path]').forEach(el =>
      el.classList.toggle('active', el.dataset.path === path)
    );
  } catch (e) { console.error('Open file failed', e); }
}

function closeEditor() {
  document.getElementById('kb-file-view').style.display  = 'none';
  document.getElementById('kb-graph-view').style.display = 'flex';
  currentFilePath = null;
  setTimeout(_resizeCanvas, 50);
}

/* ─── Save ───────────────────────────────────────────────────────────── */
async function saveVaultFile() {
  if (!currentFilePath) return;
  const editor  = document.getElementById('kb-editor');
  const content = domToMarkdown(editor);
  try {
    await fetch('/api/vault/file', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ path: currentFilePath, content }),
    });
    editorDirty = false;
    const btn = document.getElementById('kb-save-btn');
    btn.textContent = 'Saved ✓';
    setTimeout(() => { btn.textContent = 'Save'; }, 1800);
    loadGraphData();
  } catch (e) { console.error('Save failed', e); }
}

/* ─── DOM → Markdown ─────────────────────────────────────────────────── */
function domToMarkdown(root) {
  function walk(node) {
    if (node.nodeType === 3) return node.textContent.replace(/\n/g, ' ');
    if (node.nodeType !== 1) return '';
    const tag      = node.tagName.toLowerCase();
    const kids     = () => Array.from(node.childNodes).map(walk).join('');
    const trimKids = () => kids().trim();
    switch (tag) {
      case 'h1': return `\n# ${trimKids()}\n\n`;
      case 'h2': return `\n## ${trimKids()}\n\n`;
      case 'h3': return `\n### ${trimKids()}\n\n`;
      case 'h4': return `\n#### ${trimKids()}\n\n`;
      case 'h5': return `\n##### ${trimKids()}\n\n`;
      case 'h6': return `\n###### ${trimKids()}\n\n`;
      case 'strong': case 'b': { const t=trimKids(); return t?`**${t}**`:''; }
      case 'em':     case 'i': { const t=trimKids(); return t?`_${t}_`:''; }
      case 'del': case 's': case 'strike': { const t=trimKids(); return t?`~~${t}~~`:''; }
      case 'u': return trimKids();
      case 'a': return `[${trimKids()}](${node.getAttribute('href')||'#'})`;
      case 'code':
        if (node.closest('pre')) return node.textContent;
        return `\`${node.textContent}\``;
      case 'pre': {
        const codeEl = node.querySelector('code');
        const raw    = codeEl ? codeEl.textContent : node.textContent;
        const lang   = (codeEl?.className.match(/language-(\w+)/)||[])[1]||'';
        return `\n\`\`\`${lang}\n${raw.trim()}\n\`\`\`\n\n`;
      }
      case 'p':  { const t=trimKids(); return t?`${t}\n\n`:''; }
      case 'br': return '\n';
      case 'hr': return '\n---\n\n';
      case 'ul': {
        const items = Array.from(node.children).filter(n=>n.tagName==='LI')
          .map(li=>`- ${walk(li).replace(/\n+$/,'')}`).join('\n');
        return `\n${items}\n\n`;
      }
      case 'ol': {
        const items = Array.from(node.children).filter(n=>n.tagName==='LI')
          .map((li,i)=>`${i+1}. ${walk(li).replace(/\n+$/,'')}`).join('\n');
        return `\n${items}\n\n`;
      }
      case 'li': return kids();
      case 'blockquote': {
        const inner = trimKids();
        return '\n' + inner.split('\n').map(l=>`> ${l}`).join('\n') + '\n\n';
      }
      case 'table': {
        const rows = Array.from(node.querySelectorAll('tr'));
        if (!rows.length) return '';
        const toRow = r => {
          const cells = Array.from(r.querySelectorAll('th,td'))
            .map(c=>c.textContent.trim().replace(/\|/g,'\\|'));
          return `| ${cells.join(' | ')} |`;
        };
        const lines = rows.map(toRow);
        const cols  = rows[0].querySelectorAll('th,td').length;
        lines.splice(1, 0, `| ${Array(cols).fill('---').join(' | ')} |`);
        return `\n${lines.join('\n')}\n\n`;
      }
      case 'div':  { const c=kids(); return c.endsWith('\n')?c:c+'\n'; }
      case 'span': return kids();
      default:     return kids();
    }
  }
  return walk(root).replace(/\n{3,}/g,'\n\n').trim()+'\n';
}

/* ─── Toolbar commands (WYSIWYG) ─────────────────────────────────────── */
function _edFocus() { document.getElementById('kb-editor').focus(); }

function tbCmd(e, cmd) {
  e.preventDefault(); _edFocus();
  document.execCommand('styleWithCSS', false, false);
  document.execCommand(cmd, false, null);
  _markDirty();
}
function tbBlock(e, tag) {
  e.preventDefault(); _edFocus();
  document.execCommand('formatBlock', false, tag);
  _markDirty();
}
function tbInlineCode(e) {
  e.preventDefault(); _edFocus();
  const sel = window.getSelection();
  if (!sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  const code  = document.createElement('code');
  if (range.collapsed) {
    code.textContent = 'code';
    range.insertNode(code);
    const r2 = document.createRange();
    r2.selectNodeContents(code);
    sel.removeAllRanges(); sel.addRange(r2);
  } else { try { range.surroundContents(code); } catch {} }
  _markDirty();
}
function tbCodeBlock(e) {
  e.preventDefault(); _edFocus();
  const sel   = window.getSelection();
  if (!sel.rangeCount) return;
  const range = sel.getRangeAt(0);
  const pre   = document.createElement('pre');
  const code  = document.createElement('code');
  code.textContent = range.extractContents().textContent || 'code here';
  pre.appendChild(code);
  range.insertNode(pre);
  const r2 = document.createRange();
  r2.selectNodeContents(code);
  sel.removeAllRanges(); sel.addRange(r2);
  _markDirty();
}
function tbLink(e) {
  e.preventDefault(); _edFocus();
  const sel  = window.getSelection();
  const url  = prompt('URL:', 'https://');
  if (!url) return;
  if (sel.toString()) {
    document.execCommand('createLink', false, url);
  } else {
    const txt = prompt('Link text:', 'link') || 'link';
    document.execCommand('insertHTML', false, `<a href="${url}">${txt}</a>`);
  }
  _markDirty();
}
function tbTable(e) {
  e.preventDefault(); _edFocus();
  const rows = 3, cols = 3;
  let html = '<table>';
  html += '<tr>' + Array(cols).fill('<th>Header</th>').join('') + '</tr>';
  for (let r = 0; r < rows-1; r++)
    html += '<tr>' + Array(cols).fill('<td>Cell</td>').join('') + '</tr>';
  html += '</table><p><br></p>';
  document.execCommand('insertHTML', false, html);
  _markDirty();
}

function _markDirty() {
  if (!editorDirty) {
    editorDirty = true;
    const btn = document.getElementById('kb-save-btn');
    if (btn) btn.textContent = 'Save •';
  }
}

/* ─── Wire up events ─────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  // Editor input tracking
  const editor = document.getElementById('kb-editor');
  if (editor) {
    editor.addEventListener('input', _markDirty);
    editor.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') { e.preventDefault(); saveVaultFile(); }
      if (e.key === 'Tab') { e.preventDefault(); document.execCommand('insertText', false, '  '); }
    });
  }

  // Modal live preview
  const nameInput  = document.getElementById('kg-modal-name');
  const folderSel  = document.getElementById('kg-modal-folder');
  if (nameInput) nameInput.addEventListener('input',  _updateModalPreview);
  if (folderSel) folderSel.addEventListener('change', _updateModalPreview);

  // Modal Enter key
  if (nameInput) nameInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') createFromModal();
    if (e.key === 'Escape') hideAddFileModal();
  });

  // Esc cancels connect mode
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && graph?.mode === 'connect') {
      graph.connectSource = null;
      setGraphMode('navigate');
    }
    if (e.key === 'Escape' && document.getElementById('kg-modal').style.display !== 'none') {
      document.getElementById('kg-modal').style.display = 'none';
    }
  });
});
