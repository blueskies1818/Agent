# Glass AI

A glassmorphism AI chat interface with an Obsidian-style knowledge graph, vault file manager, and recurring tasks panel — powered by Claude.

![Stack](https://img.shields.io/badge/Claude-claude--sonnet--4--6-blueviolet) ![Node](https://img.shields.io/badge/Node.js-ESM-green) ![Express](https://img.shields.io/badge/Express-4.x-lightgrey)

---

## Overview

Glass AI is the **front-end control surface** for a larger two-project system. It sits alongside a separate autonomous AI agent (the "Agent" project) and the two share the same directories on disk:

```
Agent project (Python / LangGraph)
└── workspace/
    └── vault/          ← Obsidian knowledge base  ─┐  both projects
└── scheduled/          ← Recurring task JSON files ─┤  read & write
                                                      │  these same dirs
Glass AI (Node / Express)                             │
└── config.json ────────────────────────────────────┘
      vault:     /path/to/agent/workspace/vault
      scheduled: /path/to/agent/scheduled
```

Glass AI gives you a visual browser for everything the agent writes. You can read the agent's notes, edit them, reorganise folders, queue new tasks, and watch the task list update live as the agent picks them up — all from a browser tab.

---

## How the Two Projects Connect

### Shared vault (knowledge base)

The agent reads and writes `.md` files to its vault as long-term memory. Glass AI mounts the same vault directory and exposes it as a live knowledge graph and file editor. Changes you make in Glass AI (renaming files, adding wikilinks, reorganising folders) are immediately visible to the agent the next time it reads from the vault.

The `index.json` file at the vault root acts as a directory for the agent's RAG (retrieval-augmented generation) pipeline. Each entry tells the agent what a folder contains and which keywords should trigger retrieval from it. When you create or move folders in Glass AI the `index.json` is updated automatically so the agent's memory index stays in sync.

### Shared task queue (scheduled tasks)

The agent's scheduler polls a `scheduled/` directory for `.json` task files and executes them on a cron-like schedule. The Tasks panel in Glass AI reads and writes those same files, giving you a GUI to create, edit, and delete scheduled tasks without touching the filesystem directly. The task list in the UI refreshes in real time via SSE whenever the agent picks up or modifies a task file.

### Agent HTTP API (future integration)

The agent also exposes its own HTTP server (default port `9000`) with endpoints for submitting tasks, streaming results, and querying task status. Glass AI does not call this API yet, but the Chat panel is the natural place to wire it up. See the roadmap section below.

---

## Features

### Chat
- Streaming responses via Server-Sent Events
- Full markdown rendering (code blocks, lists, bold, etc.)
- Conversation history preserved per session

### Knowledge Graph (Vault)
- Force-directed graph of your Obsidian vault rendered on HTML5 Canvas
- Nodes represent `.md` files; edges represent `[[wikilinks]]`
- **Navigate mode** — click nodes to open files, drag to reposition, right-click to unpin
- **Connect mode** — click two nodes to add a wikilink; clicking an already-linked pair removes it
- **Zoom to folder** — right-click a folder in the sidebar to isolate its contents in the graph and file tree
- Home / Back navigation buttons to exit zoom state
- Create files or folders directly from the graph modal; folders are registered in `index.json` for RAG indexing

### Vault File Manager (Sidebar)
- Collapsible folder tree for all `.md` files in the vault
- Click a file to open it in the built-in markdown editor; edits save back to disk
- Hover any file or folder to reveal a delete button
- Drag files and folders onto other folders (or the root) to move them; `index.json` is updated automatically for folder moves

### Tasks
- Read/write recurring task definitions stored as `.json` files in a configurable directory
- Create, edit, and delete tasks from a form panel
- Live file-watch via SSE — the task list refreshes automatically when files change on disk

---

## Setup

### 1. Install dependencies

```bash
npm install
```

### 2. Set your Anthropic API key

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Configure vault and tasks paths

Create `config.json` in the project root and point it at the agent's directories:

```json
{
  "vault": "/path/to/agent/workspace/vault",
  "scheduled": "/path/to/agent/scheduled"
}
```

Both keys are optional — omitting them disables the vault and tasks panels respectively.

### 4. Start the server

```bash
npm start        # production
npm run dev      # auto-restart on file changes (Node --watch)
```

Then open [http://localhost:3000](http://localhost:3000).

If you also want the agent running at the same time, start it separately from the Agent project directory:

```bash
./start.sh       # from the Agent project root
```

The two servers are independent — Glass AI on port `3000`, the agent on port `9000` — and communicate only through the shared filesystem.

---

## Project Structure

```
glass-ai/
├── server.js          # Express server — chat, vault, and tasks API
├── config.json        # Vault and tasks directory paths (gitignore this)
├── public/
│   ├── index.html     # Single-page shell
│   ├── css/
│   │   └── styles.css # Glassmorphism design system + all component styles
│   └── js/
│       ├── app.js     # App bootstrap, sidebar tab switching, theme toggle
│       ├── vault.js   # Knowledge graph, vault tree, file editor
│       ├── graph.js   # Force-directed canvas graph engine
│       └── tasks.js   # Tasks panel, form, and SSE watch
└── package.json
```

---

## Roadmap — Deeper Agent Integration

The Chat panel currently talks directly to Claude via the Anthropic API. The goal is to replace that with a full connection to the Python agent and its harness, so every chat message becomes a task the agent can act on with its complete toolset — shell access, web search, memory retrieval, file writes, skill execution, and scheduled follow-ups.

The agent exposes these endpoints for that:

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `http://localhost:9000/queue` | Submit a prompt; returns `task_id` immediately |
| `GET` | `http://localhost:9000/stream/<task_id>` | SSE stream of typed agent output chunks |
| `GET` | `http://localhost:9000/tasks/<task_id>` | Task status and final result |
| `POST` | `http://localhost:9000/schedule` | Write a scheduled task directly via HTTP |

---

### Phase 1 — Connect the Agent

1. Add an agent mode toggle to the chat header in `index.html`
2. In agent mode, `app.js` POSTs to `/queue` instead of `/api/chat`, gets back a `task_id`
3. Open an SSE connection to `/stream/<task_id>` and pipe chunks into the existing chat message renderer — the streaming UI already handles this format
4. Expose task status (running / done / error) as a badge on the message

---

### Phase 2 — Communication and Transparency

Give the interface visibility into what the agent is actually doing as it runs, not just the final answer:

- **Thinking panel** — the agent's planner emits a reasoning trace before it acts; surface this as a collapsible "thinking..." block above each response, similar to how extended thinking works in Claude
- **Skill call feed** — each tool/skill the worker executes (shell command, web search, memory read, etc.) appears as a live event card in the message thread so you can see exactly what actions were taken and in what order
- **Regenerate** — a regenerate button re-submits the same prompt to `/queue` and replaces the previous message in the thread; useful when the agent takes a wrong path
- **Cancel** — a stop button sends `DELETE /tasks/<task_id>` to cancel a running task mid-execution

---

### Phase 3 — Management Mode

A dedicated management view that gives direct visibility and control into the agent's internal workspace:

- **Live agent log** — tail `memory/logs/agent.log` streamed to the browser via SSE; filterable by level (info / tool / error)
- **Memory browser** — browse and edit the agent's RAG vector store buckets; see what chunks were retrieved for the last query
- **Skill manager** — list installed skills and mods, view their docs, enable/disable them, and trigger the agent to write new ones
- **Session history** — browse past sessions stored in `workspace/sessions/`; replay any session as a read-only transcript
- **Resource monitor** — CPU, memory, and Docker sandbox status polled from the agent's `/status` endpoint and displayed as a compact status bar

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/chat` | Streaming SSE chat (Claude direct) |
| `GET` | `/api/vault/tree` | Full vault directory tree |
| `GET` | `/api/vault/file?path=` | Read a file |
| `POST` | `/api/vault/file` | Write / save a file |
| `POST` | `/api/vault/file/new` | Create a new file |
| `DELETE` | `/api/vault/file?path=` | Delete a file |
| `POST` | `/api/vault/folder` | Create a folder (updates `index.json`) |
| `DELETE` | `/api/vault/folder?path=` | Delete a folder recursively (updates `index.json`) |
| `POST` | `/api/vault/move` | Move a file or folder |
| `GET` | `/api/vault/links` | Wikilink graph (nodes + edges) |
| `GET` | `/api/tasks` | List all task files |
| `POST` | `/api/tasks` | Create a task |
| `PUT` | `/api/tasks/:filename` | Update a task |
| `DELETE` | `/api/tasks/:filename` | Delete a task |
| `GET` | `/api/tasks/watch` | SSE stream — fires `refresh` on any task file change |

---

## index.json

When folders are created or moved, Glass AI maintains an `index.json` file at the vault root. This registry tells the agent's RAG pipeline what each folder contains and which keywords should trigger retrieval from it.

```json
{
  "updated_at": "2026-04-29T12:00:00.000Z",
  "buckets": {
    "Projects": {
      "path": "Projects",
      "description": "Active project notes",
      "content_count": 0,
      "tags": ["projects", "active"],
      "created_at": "2026-04-29T12:00:00.000Z"
    }
  }
}
```

Glass AI writes this file automatically. The agent reads it at startup and whenever its memory system re-indexes the vault.

---

## Requirements

- Node.js 18+ (ESM support)
- An Anthropic API key
- The Agent project for vault/task functionality (Glass AI works standalone for chat only)
