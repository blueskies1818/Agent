/* ─── ForceGraph — Canvas force-directed graph ─────────────────────────── */
class ForceGraph {
  constructor(canvas, onOpen, onSelect) {
    this.canvas   = canvas;
    this.ctx      = canvas.getContext('2d');
    this.onOpen   = onOpen;    // node clicked-to-open
    this.onSelect = onSelect;  // node selected (highlight)
    this.nodes    = [];
    this.links    = [];

    this.px    = 0; this.py = 0;
    this.scale = 1;
    this.running       = false;
    this.hoveredNode   = null;
    this.draggingNode  = null;
    this.selectedNode  = null;
    this.connectSource = null;
    this.panning       = false;
    this.mode          = 'navigate'; // 'navigate' | 'connect'
    this._neighbors    = new Set();
    this._neighborLinks = new Set();
    this._lastX = 0; this._lastY = 0;
    this._mouseDownX = 0; this._mouseDownY = 0;
    this._mouseDownNode = null;

    canvas.addEventListener('mousemove',    this._onMouseMove.bind(this));
    canvas.addEventListener('mousedown',    this._onMouseDown.bind(this));
    canvas.addEventListener('mouseup',      this._onMouseUp.bind(this));
    canvas.addEventListener('mouseleave',   this._onMouseLeave.bind(this));
    canvas.addEventListener('click',        this._onClick.bind(this));
    canvas.addEventListener('contextmenu',  this._onContextMenu.bind(this));
    canvas.addEventListener('wheel',        this._onWheel.bind(this), { passive: false });
  }

  setData(nodes, links) {
    // Preserve positions of existing nodes so a reload doesn't scatter them
    const prev = Object.fromEntries(this.nodes.map(n => [n.id, n]));

    this.nodes = nodes.map(n => {
      const p = prev[n.id];
      return {
        ...n,
        x:      p ? p.x  : (Math.random() - 0.5) * 900,
        y:      p ? p.y  : (Math.random() - 0.5) * 900,
        vx:     p ? p.vx : (Math.random() - 0.5) * 2,
        vy:     p ? p.vy : (Math.random() - 0.5) * 2,
        r:      5 + Math.log1p(n.degree || 0) * 3.2,
        pinned: p ? p.pinned : false,
      };
    });

    const nodeById = Object.fromEntries(this.nodes.map(n => [n.id, n]));
    this.links = links
      .map(l => ({ source: nodeById[l.source], target: nodeById[l.target] }))
      .filter(l => l.source && l.target);

    // Re-select previously selected node if still present
    if (this.selectedNode) {
      this.selectedNode = nodeById[this.selectedNode.id] || null;
      this._buildNeighbors();
    }

    if (!this.running) {
      this.px = this.canvas.width  / 2;
      this.py = this.canvas.height / 2;
    }
    this.start();
  }

  setMode(mode) {
    this.mode = mode;
    if (mode === 'navigate') {
      this.connectSource = null;
      this.canvas.style.cursor = 'grab';
    } else {
      this.canvas.style.cursor = 'crosshair';
    }
  }

  selectNode(node) {
    this.selectedNode = node;
    this._buildNeighbors();
    if (this.onSelect) this.onSelect(node);
  }

  _buildNeighbors() {
    this._neighbors     = new Set();
    this._neighborLinks = new Set();
    const n = this.selectedNode;
    if (!n) return;
    this.links.forEach(l => {
      if (l.source === n || l.target === n) {
        this._neighborLinks.add(l);
        this._neighbors.add(l.source === n ? l.target : l.source);
      }
    });
  }

  start() {
    if (this.running) return;
    this.running = true;
    this._loop();
  }

  stop() { this.running = false; }

  _loop() {
    if (!this.running) return;
    this._tick();
    this._render();
    requestAnimationFrame(() => this._loop());
  }

  _tick() {
    const repulsion = 5500;
    const springK   = 0.014;
    const restLen   = 180;
    const gravity   = 0.010;
    const damping   = 0.86;

    this.nodes.forEach(n => { n.fx = 0; n.fy = 0; });

    for (let i = 0; i < this.nodes.length; i++) {
      for (let j = i + 1; j < this.nodes.length; j++) {
        const a = this.nodes[i], b = this.nodes[j];
        const dx = b.x - a.x, dy = b.y - a.y;
        const dist = Math.sqrt(dx*dx + dy*dy) || 0.1;
        const f  = repulsion / (dist*dist);
        const fx = (dx/dist)*f, fy = (dy/dist)*f;
        a.fx -= fx; a.fy -= fy;
        b.fx += fx; b.fy += fy;
      }
    }

    this.links.forEach(({ source: a, target: b }) => {
      const dx = b.x - a.x, dy = b.y - a.y;
      const dist = Math.sqrt(dx*dx + dy*dy) || 0.1;
      const f  = springK * (dist - restLen);
      const fx = (dx/dist)*f, fy = (dy/dist)*f;
      a.fx += fx; a.fy += fy;
      b.fx -= fx; b.fy -= fy;
    });

    this.nodes.forEach(n => { n.fx -= n.x * gravity; n.fy -= n.y * gravity; });

    this.nodes.forEach(n => {
      if (n === this.draggingNode || n.pinned) return;
      n.vx = (n.vx + n.fx) * damping;
      n.vy = (n.vy + n.fy) * damping;
      n.x += n.vx;
      n.y += n.vy;
    });
  }

  _toScreen(x, y) {
    return { sx: x * this.scale + this.px, sy: y * this.scale + this.py };
  }
  _toWorld(sx, sy) {
    return { x: (sx - this.px) / this.scale, y: (sy - this.py) / this.scale };
  }

  _colors() {
    const dark = document.documentElement.getAttribute('data-theme') !== 'light';
    return {
      nodeBase:      dark ? '#9b87f5' : '#7c3aed',
      nodeSelected:  dark ? '#f0abfc' : '#a21caf',
      nodeConnect:   '#f59e0b',
      nodeNeighbor:  dark ? '#c4b8fb' : '#6d28d9',
      nodePinned:    dark ? '#a78bfa' : '#6d28d9',
      nodeHot:       dark ? '#c4b8fb' : '#7c3aed',
      nodeDim:       dark ? '#9b87f5' : '#7c3aed',
      nodeStroke:    dark ? 'rgba(255,255,255,0.20)' : 'rgba(255,255,255,0.60)',
      pinnedRing:    dark ? 'rgba(167,139,250,0.55)' : 'rgba(109,40,217,0.45)',
      selectedRing:  dark ? 'rgba(240,171,252,0.70)' : 'rgba(162,28,175,0.55)',
      connectRing:   'rgba(245,158,11,0.70)',
      edgeNormal:    dark ? 'rgba(155,135,245,0.22)' : 'rgba(124,58,237,0.20)',
      edgeHighlight: dark ? 'rgba(240,171,252,0.70)' : 'rgba(162,28,175,0.55)',
      edgeDim:       dark ? 'rgba(155,135,245,0.04)' : 'rgba(124,58,237,0.04)',
      glowSelected:  dark ? 'rgba(240,171,252,0.40)' : 'rgba(162,28,175,0.25)',
      glowConnect:   'rgba(245,158,11,0.45)',
      glowHot:       dark ? 'rgba(155,135,245,0.45)' : 'rgba(124,58,237,0.30)',
      glowEnd:       'rgba(155,135,245,0)',
      labelNormal:   dark ? 'rgba(238,238,255,0.72)' : 'rgba(30,27,75,0.72)',
      labelBright:   dark ? 'rgba(238,238,255,1)'    : 'rgba(30,27,75,1)',
      labelDim:      dark ? 'rgba(238,238,255,0.20)' : 'rgba(30,27,75,0.20)',
    };
  }

  _render() {
    const ctx = this.ctx;
    const W = this.canvas.width, H = this.canvas.height;
    ctx.clearRect(0, 0, W, H);

    const c   = this._colors();
    const sel = this.selectedNode;
    const con = this.connectSource;
    const hasSelection = sel !== null;
    const hasConnect   = con !== null;

    // ── Connect-mode ghost line from source to cursor ──────────────────
    if (hasConnect && this._cursorX !== undefined) {
      const { sx, sy } = this._toScreen(con.x, con.y);
      ctx.beginPath();
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = c.connectRing;
      ctx.lineWidth   = 1.5;
      ctx.moveTo(sx, sy);
      ctx.lineTo(this._cursorX, this._cursorY);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // ── Edges ──────────────────────────────────────────────────────────
    this.links.forEach(l => {
      const { sx: ax, sy: ay } = this._toScreen(l.source.x, l.source.y);
      const { sx: bx, sy: by } = this._toScreen(l.target.x, l.target.y);
      const highlight = hasSelection && this._neighborLinks.has(l);
      const dim       = hasSelection && !highlight;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.strokeStyle = highlight ? c.edgeHighlight : dim ? c.edgeDim : c.edgeNormal;
      ctx.lineWidth   = highlight ? 1.5 : 1;
      ctx.stroke();
    });

    // ── Nodes ──────────────────────────────────────────────────────────
    this.nodes.forEach(n => {
      const { sx, sy } = this._toScreen(n.x, n.y);
      const r    = n.r * this.scale;
      const hot  = n === this.hoveredNode;
      const isSel = n === sel;
      const isCon = n === con;
      const isNeighbor = hasSelection && this._neighbors.has(n);
      const isDim = hasSelection && !isSel && !isNeighbor;
      const isDrag = n === this.draggingNode;

      // Glow
      const glowColor = isCon ? c.glowConnect : isSel ? c.glowSelected : c.glowHot;
      if (hot || isSel || isCon || isDrag) {
        const g = ctx.createRadialGradient(sx, sy, 0, sx, sy, r * 3);
        g.addColorStop(0, glowColor);
        g.addColorStop(1, c.glowEnd);
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(sx, sy, r * 3, 0, Math.PI * 2);
        ctx.fill();
      }

      // Outer ring for selected / connect-source / pinned
      if (isCon) {
        ctx.beginPath();
        ctx.arc(sx, sy, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = c.connectRing;
        ctx.lineWidth   = 2;
        ctx.stroke();
      } else if (isSel) {
        ctx.beginPath();
        ctx.arc(sx, sy, r + 4, 0, Math.PI * 2);
        ctx.strokeStyle = c.selectedRing;
        ctx.lineWidth   = 2;
        ctx.stroke();
      } else if (n.pinned && !isDrag) {
        ctx.beginPath();
        ctx.arc(sx, sy, r + 3, 0, Math.PI * 2);
        ctx.strokeStyle = c.pinnedRing;
        ctx.lineWidth   = 1.5;
        ctx.stroke();
      }

      const isolated = (n.degree || 0) === 0;
      ctx.globalAlpha = isDim ? (isolated ? 0.12 : 0.18) : (isolated && !isSel ? 0.55 : 1);

      ctx.beginPath();
      ctx.arc(sx, sy, r, 0, Math.PI * 2);
      ctx.fillStyle = isCon ? c.nodeConnect
        : isSel     ? c.nodeSelected
        : isNeighbor ? c.nodeNeighbor
        : (hot || isDrag) ? c.nodeHot
        : n.pinned  ? c.nodePinned
        : c.nodeBase;
      ctx.fill();
      ctx.strokeStyle = c.nodeStroke;
      ctx.lineWidth   = 1;
      ctx.stroke();
      ctx.globalAlpha = 1;

      // Label
      if (this.scale > 0.45 || hot || isSel) {
        const fs = Math.max(9, 11 * this.scale);
        ctx.fillStyle = isDim ? c.labelDim
          : (hot || isSel || isCon || isNeighbor) ? c.labelBright
          : c.labelNormal;
        ctx.font      = `${fs}px Inter, sans-serif`;
        ctx.textAlign = 'center';
        const label   = n.label.length > 22 ? n.label.slice(0, 20) + '…' : n.label;
        ctx.globalAlpha = isDim ? 0.2 : 1;
        ctx.fillText(label, sx, sy + r + fs * 1.2);
        ctx.globalAlpha = 1;
      }
    });
  }

  _nodeAt(sx, sy) {
    for (const n of this.nodes) {
      const { sx: nx, sy: ny } = this._toScreen(n.x, n.y);
      const r = n.r * this.scale + 6;
      if ((sx - nx)**2 + (sy - ny)**2 <= r*r) return n;
    }
    return null;
  }

  // ── Event handlers ───────────────────────────────────────────────────

  _onMouseMove(e) {
    const { sx, sy } = this._evPos(e);
    this._cursorX = sx; this._cursorY = sy;

    if (this.panning) {
      this.px += sx - this._lastX;
      this.py += sy - this._lastY;
      this._lastX = sx; this._lastY = sy;
      return;
    }
    if (this.draggingNode) {
      const w = this._toWorld(sx, sy);
      this.draggingNode.x  = w.x; this.draggingNode.y  = w.y;
      this.draggingNode.vx = 0;   this.draggingNode.vy = 0;
      return;
    }
    const n = this._nodeAt(sx, sy);
    this.hoveredNode = n;
    if (this.mode === 'connect') {
      this.canvas.style.cursor = n ? 'crosshair' : 'crosshair';
    } else {
      this.canvas.style.cursor = n ? 'pointer' : 'grab';
    }
  }

  _onMouseDown(e) {
    if (e.button !== 0) return; // ignore right-click / middle-click
    const { sx, sy } = this._evPos(e);
    this._mouseDownX = sx; this._mouseDownY = sy;
    const n = this._nodeAt(sx, sy);
    this._mouseDownNode = n;

    if (n && this.mode === 'navigate') {
      this.draggingNode = n;
      this.canvas.style.cursor = 'grabbing';
    } else if (!n) {
      this.panning = true;
      this._lastX  = sx; this._lastY = sy;
      this.canvas.style.cursor = 'grabbing';
    }
  }

  _onMouseUp(e) {
    if (this.draggingNode) {
      this.draggingNode.pinned = true;
      this.draggingNode.vx     = 0;
      this.draggingNode.vy     = 0;
    }
    this.draggingNode = null;
    this.panning      = false;
    const { sx, sy } = this._evPos(e);
    const n = this._nodeAt(sx, sy);
    this.hoveredNode = n;
    this.canvas.style.cursor = this.mode === 'connect' ? 'crosshair'
      : n ? 'pointer' : 'grab';
  }

  _onMouseLeave() {
    if (this.draggingNode) {
      this.draggingNode.pinned = true;
      this.draggingNode.vx = this.draggingNode.vy = 0;
    }
    this.draggingNode = null;
    this.panning      = false;
    this.hoveredNode  = null;
    this._cursorX = undefined;
    this.canvas.style.cursor = this.mode === 'connect' ? 'crosshair' : 'grab';
  }

  _onClick(e) {
    const { sx, sy } = this._evPos(e);
    const dx = sx - this._mouseDownX, dy = sy - this._mouseDownY;
    if (Math.sqrt(dx*dx + dy*dy) > 6) return; // was a drag

    const n = this._nodeAt(sx, sy);

    if (this.mode === 'connect') {
      if (!n) return;
      if (!this.connectSource) {
        this.connectSource = n;
      } else if (n !== this.connectSource) {
        if (this.onConnect) this.onConnect(this.connectSource, n);
        this.connectSource = null;
      } else {
        this.connectSource = null; // cancel by clicking same node
      }
      return;
    }

    // Navigate mode
    if (!n) {
      // Click empty space → deselect
      this.selectNode(null);
      return;
    }
    if (n === this.selectedNode) {
      // Second click on selected node → open file
      if (this.onOpen) this.onOpen(n);
    } else {
      this.selectNode(n);
    }
  }

  _onContextMenu(e) {
    e.preventDefault();
    const { sx, sy } = this._evPos(e);
    const n = this._nodeAt(sx, sy);
    if (!n || this.mode !== 'navigate') return;
    n.pinned = false;
    n.vx = (Math.random() - 0.5) * 0.5;
    n.vy = (Math.random() - 0.5) * 0.5;
  }

  _onWheel(e) {
    e.preventDefault();
    const { sx, sy } = this._evPos(e);
    const factor = e.deltaY > 0 ? 0.88 : 1.14;
    this.px    = sx + (this.px - sx) * factor;
    this.py    = sy + (this.py - sy) * factor;
    this.scale = Math.max(0.08, Math.min(6, this.scale * factor));
  }

  _evPos(e) {
    const r = this.canvas.getBoundingClientRect();
    return { sx: e.clientX - r.left, sy: e.clientY - r.top };
  }

  resize(w, h) {
    const dw = w - (this.canvas.width  || 0);
    const dh = h - (this.canvas.height || 0);
    this.canvas.width  = w;
    this.canvas.height = h;
    if (!this.nodes.length) { this.px += dw/2; this.py += dh/2; }
  }
}
