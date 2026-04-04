# AI Shell Agent

An autonomous AI agent with direct shell access to an isolated Docker sandbox. The agent reasons, plans, and executes structured actions via XML — running commands, managing files, loading skills on demand, querying memory with semantic search, searching the web, and interacting with GUI applications through a headless virtual display with live streaming.

---

## Architecture

```
Agent/
├── main.py                      # Entry point — interactive REPL
├── config.py                    # All settings (providers, paths, limits, display, ports)
├── soul.md                      # Agent identity, personality, and rules
├── start.sh                     # Unified launcher — venv, deps, start
├── wipe.py                      # Wipe memory, logs, vectors, or workspace
├── requirements.txt
├── dockerfile                   # Docker sandbox image (Ubuntu + tools + Firefox)
├── docker-compose.yml           # Container config 
├── .dockerignore
├── SKILLS_ECOSYSTEM.md          # Full docs on the skills + mods architecture
│
├── agents/
│   ├── __init__.py
│   └── base.py                  # Abstract BaseAgent — provider contract
│
├── providers/                   # One file per LLM provider, loaded dynamically
│   ├── __init__.py              # Dynamic loader (load_provider)
│   ├── claude.py                # Anthropic Claude
│   └── openai.py                # OpenAI GPT
│
├── core/
│   ├── __init__.py
│   ├── xml_parser.py            # Parse think/plan/work/action tags from AI responses
│   ├── context_window.py        # Scored page stack with automatic eviction
│   └── prompt_evaluator.py      # Proactive context retrieval + skill/mod hinting
│
├── engine/                      # Agentic execution layer
│   ├── __init__.py
│   ├── state.py                 # LangGraph AgentState TypedDict
│   ├── nodes.py                 # Planner, actor, reflector + multimodal message builder
│   ├── graph.py                 # LangGraph StateGraph assembly + compilation
│   ├── loop.py                  # Session wrapper — owns context window + graph
│   ├── sandbox.py               # Shell execution backend (local or Docker)
│   ├── mod_api.py               # ModResult + memory API for all mods
│   └── frame_server.py          # Generic live frame HTTP server
│
├── memory/                      # Persistent memory layer (SQLite + ChromaDB + flat file)
│   ├── __init__.py
│   ├── db.py                    # SQLite setup, schema, row helpers
│   ├── conversation.py          # Rolling conversational memory + compression
│   ├── long_term.py             # Key-value preferences (never expires)
│   ├── sessions.py              # Session lifecycle — start, end, load, list
│   ├── task_blobs.py            # Full task detail records + searchable index
│   ├── injector_stub.py         # Context builder stub (replaced in later phases)
│   ├── memory.py                # Flat file + ChromaDB dual-write
│   ├── embedder.py              # OpenAI embeddings → ChromaDB vector store
│   ├── rag.py                   # Semantic retriever over ChromaDB
│   ├── agent.db                 # SQLite database (auto-created)
│   ├── memory.txt               # Rolling facts file (AI-managed, human-readable)
│   ├── chroma/                  # ChromaDB vector store (auto-created)
│   └── logs/                    # Per-session turn transcripts (auto-created)
│
├── mods/                        # Drop-in command modules (intercepted shell commands)
│   ├── __init__.py              # ModRouter — dynamic discovery + dispatch
│   ├── memory/
│   │   └── memory.py            # memory command — query/read/write/prefs/blobs
│   ├── web_search/
│   │   ├── web_search.py        # search_web command — search/fetch URLs
│   │   └── web_search_tool.py   # Search engine — fetch, parse, chunk, score
│   └── debug_ui/
│       ├── debug_ui.py          # debug_ui command — headless GUI interaction
│       └── viewer.py            # Live viewer app (run separately by user)
│
├── skills/                      # Skill definitions loaded on demand (.md files)
│   ├── read.md                  # View files and directory contents
│   ├── write.md                 # Create files using printf
│   ├── edit.md                  # In-place modifications with sed
│   ├── delete.md                # Safe removal of files and directories
│   ├── memory.md                # Query, read, and write persistent memory
│   ├── web_search.md            # Search the internet for current information
│   └── debug_ui.md              # Launch and interact with GUI applications
│
├── reactive/                    # Incoming communication sources (future expansion)
│   └── __init__.py
│
└── workspace/                   # AI's working directory (bind-mounted into Docker)
```

---

## Quick Start

```bash
# 1. Enter the project directory
cd Agent/

# 2. Copy and fill in API keys
cp .env.example .env

# 3. Launch (local mode — no Docker needed)
./start.sh

# 4. Launch with Docker sandbox (isolated, root access, GUI support)
SANDBOX=docker ./start.sh

# 5. Launch with a project directory synced into the sandbox
PROJECT=/home/user/my-app SANDBOX=docker ./start.sh
```

`start.sh` handles everything: creates the Python venv, installs dependencies, builds the Docker image (first time only), starts the container with the correct mount, and launches the agent REPL.

---

## How It Works

### LangGraph State Machine

Each user message runs through a three-node graph:

```
user message
     │
     ▼
 ┌─────────┐     think + plan
 │ planner │ ──→ displays [plan] to user
 └────┬────┘
      │
 ┌────▼────┐     execute actions, display results
 │  actor  │ ◄─────────────────────────────────┐
 └────┬────┘                                   │
      │                                        │
 ┌────▼──────┐   done or turn limit?           │
 │ reflector │ ──→ yes → END                   │
 └───────────┘ ──→ no  ────────────────────────┘
```

---

### Tags

```xml
<think>Internal reasoning — shown dimmed, never fed back.</think>

<plan>
  1. Check if the file exists
  2. Write the content
  3. Verify with cat
  4. Summarise and confirm to the user
</plan>

<work>What I am doing right now — shown as a status line.</work>

<action type="shell"><command>printf 'hello\n' > file.txt</command></action>
```

---

### Docker Sandbox

Shell commands run inside an isolated Docker container. The AI has root access inside the container but cannot see or touch the host filesystem except for the mounted workspace.

```
HOST (your machine)                    DOCKER CONTAINER
├── Agent/                             ├── /workspace/ ← bind-mounted, syncs both ways
│   ├── engine/     ✗ unreachable      │   ├── project files
│   ├── config.py   ✗ unreachable      │   └── (AI reads and writes here)
│   ├── .env        ✗ unreachable      │
│   └── workspace/ ─── bind mount ──>  │── root access (apt-get, pip, etc.)
│                                      │── Xvfb virtual display
│                                      └── Firefox, dev tools, etc.
```

The AI can install packages, run dev servers, and launch GUI applications — all inside the sandbox. A container restart resets everything except `/workspace`.

---

### Skills & Mods

Two extensibility layers — see `docs/skills&mods_info.md` for full docs.

**Skills** are `.md` files in `skills/` — documentation the agent loads on demand.

**Mods** are Python packages in `mods/` — they intercept shell commands before they hit the real shell.

```
<action type="shell"><command>memory -query "PyQt6"</command></action>
       │
       ▼
  ModRouter → match "memory" → mods/memory/memory.handle()  (in-process)

<action type="shell"><command>ls -la</command></action>
       │
       ▼
  ModRouter → no match → docker exec agent-sandbox bash -c "ls -la"
```

| Skill | Mod | What it does |
|-------|-----|-------------|
| `read.md` | — | View files (real shell) |
| `write.md` | — | Create files (real shell) |
| `edit.md` | — | Modify files (real shell) |
| `delete.md` | — | Remove files (real shell) |
| `memory.md` | `mods/memory/` | Query/read/write persistent memory |
| `web_search.md` | `mods/web_search/` | Search the web, fetch URLs |
| `debug_ui.md` | `mods/debug_ui/` | Headless GUI interaction + live viewer |

---

### Debug UI

The agent can launch GUI applications inside the container's virtual display, take screenshots, and interact via mouse and keyboard:

```
debug_ui -start "firefox about:blank"    Launch app, get screenshot
debug_ui -click 640 400                  Click, get screenshot
debug_ui -type "hello"                   Type, get screenshot
debug_ui -key Return                     Press key, get screenshot
debug_ui -close                          Kill app + display
```

Every command returns a screenshot — the AI sees what happened immediately.

**Live viewer:** When the display starts, a frame server launches automatically at `http://localhost:9222`. Watch what the AI sees in real time:

```bash
# In a separate terminal
python mods/debug_ui/viewer.py
python mods/debug_ui/viewer.py --fps 30

# Or just open in any browser
http://localhost:9222
```

The frame server is generic (`engine/frame_server.py`) — any mod can register a capture function. The viewer doesn't know about debug_ui specifically; it just shows whatever frames are being served.

---

### Memory

Multi-layered memory that persists across sessions:

| Store | Purpose | Accessed via |
|-------|---------|-------------|
| `long_term` table | User preferences | `memory -prefs`, `memory -pref key value` |
| `conversation` table | Turns, summaries, compressions | `memory -query "..."` |
| `task_blobs` table | Completed task records | `memory -blobs`, `memory -blob name` |
| `memory.txt` | Flat file fallback | `memory -read`, `memory -write "..."` |
| `chroma/` | ChromaDB vector store | `memory -query "..."` (via RAG) |

The `memory -query` command searches all stores simultaneously. Before each turn, the prompt evaluator also runs semantic retrieval automatically.

Mods can write to memory via `engine/mod_api.py`:

```python
from engine.mod_api import log_action, save_fact
log_action("clicked submit button at (640, 380)")  # lightweight, survives eviction
save_fact("user's project uses FastAPI")            # persists across sessions
```

---

### Context Window

Scored page stack with automatic eviction:

```
[SYSTEM  | score 0.91]  sandbox path
[AGENT   | score 0.83]  last shell output
[MEMORY  | score 0.74]  retrieved facts from RAG
[SKILL   | score 0.62]  skill definition
──────────────── eviction threshold ────────────────
[MEMORY  | score 0.18]  evicted — too old, too irrelevant
```

Score = `relevance × 0.6 + recency × 0.4`. Screenshots from debug_ui get evicted naturally (large, decaying recency) while `log_action()` text descriptions persist (small, high relevance).

---

### Providers

Loaded dynamically from `providers/` based on `ACTIVE_PROVIDER` in `config.py`. The multimodal message builder auto-detects the provider and formats image blocks correctly (Anthropic vs OpenAI use different structures).

---

## Configuration

All settings live in `config.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ACTIVE_PROVIDER` | `"openai"` | Provider (`claude`, `openai`) |
| `ACTIVE_TIER` | `"smart"` | Model tier (`fast` or `smart`) |
| `SANDBOX_MODE` | `"local"` | `"local"` or `"docker"` |
| `PROJECT_DIR` | `None` | Host directory to mount as workspace |
| `GRAPH_TURN_LIMIT` | `None` | Max actor cycles per message |
| `MAX_TURNS` | `30` | Hard stop on total loop iterations |
| `SHELL_TIMEOUT` | `30` | Seconds before a shell command is killed |
| `MAX_CONTEXT_TOKENS` | `8000` | Context window token budget |
| `RELEVANCE_WEIGHT` | `0.6` | Weight of relevance in page scoring |
| `RECENCY_WEIGHT` | `0.4` | Weight of recency in page scoring |
| `RAG_TOP_K` | `5` | Memory pages retrieved per user message |
| `RAG_MIN_SCORE` | `0.4` | Minimum similarity score for retrieval |
| `WEB_SEARCH_SOURCES` | `3` | Default pages to fetch per web search |
| `DISPLAY_RESOLUTION` | `"1280x800x24"` | Virtual display resolution |
| `FRAME_SERVER_PORT` | `9222` | Live viewer HTTP port |
| `UI_SETTLE_DELAY` | `1.5` | Seconds to wait after UI actions |

---

## Environment Variables

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

---

## Wiping Data

```bash
python wipe.py                   # memory + logs + vectors
python wipe.py all               # everything including workspace
python wipe.py logs              # just session logs
python wipe.py memory vectors    # specific targets
python wipe.py all --yes         # skip confirmation
```

---

## Adding a New Mod

1. Create `mods/my_tool/my_tool.py` — define `NAME`, `DESCRIPTION`, `handle(args, raw)`
2. Add any internal helpers alongside it in the same directory
3. Create `skills/my_tool.md` — add a `description:` frontmatter field (1-liner shown in the runtime index) and document the command syntax
4. (Optional) Add a `keywords:` field to the frontmatter in the skill file — comma-separated trigger words that hint the skill proactively

No imports to update, no registration code. See `docs/skills&mods_info.md` for the full guide.

---

## Roadmap

- [x] LangGraph planner → actor → reflector loop
- [x] Docker container sandbox with root access
- [x] Project directory sync (bind mount)
- [x] Persistent memory (SQLite + ChromaDB + flat file)
- [x] Semantic memory retrieval (RAG) injected before each turn
- [x] Scored context window with automatic eviction
- [x] Eviction-triggered memory persistence
- [x] Per-turn conversation embedding
- [x] Drop-in mod system
- [x] Web search mod (DuckDuckGo)
- [x] Headless GUI mod (Xvfb + xdotool + screenshots)
- [x] Live frame streaming (viewer + browser)
- [x] Multimodal pipeline (mods can return images to the LLM)
- [x] Agent-authored skills (skill_forge mod)
- [x] Session-scoped credential cache with <<NAME>> interpolation + scrubbing
- [ ] Scheduled tasks / heartbeat (periodic background agent runs)
- [ ] Auto-distillation — LLM extracts facts from context at turn end
- [ ] LangGraph planner upgraded to multi-step goal tracking
- [ ] Workspace edit history / checkpoints
- [ ] Multi-agent support (agent spawning sub-agents)
- [ ] Reactive input sources (webhook, file watcher, socket)
- [ ] Memory summarisation + context minimisation
