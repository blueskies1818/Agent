# Overview & Quick Start

An autonomous AI agent with a planner/worker architecture and direct shell access to an isolated Docker sandbox. The planner reasons about requests and writes structured plans; the worker executes them step by step — running shell commands, managing files, querying memory, searching the web, and interacting with GUI applications through a headless virtual display. A built-in HTTP server and scheduler let external systems submit tasks and receive streamed results.

---

## Directory Tree

```
Agent/
├── main.py                      # Entry point — starts server, scheduler, REPL
├── config.py                    # All settings (providers, paths, limits, ports)
├── context_map.py               # Terminal live view of context windows + image token stats
├── wipe_All.py                  # Wipe memory, logs, vectors, or workspace
├── start.sh                     # Unified launcher — venv, deps, Docker, start
├── requirements.txt
├── dockerfile                   # Docker sandbox image (Ubuntu + tools + Firefox)
├── docker-compose.yml           # Container config
├── .dockerignore
│
├── agents/                      # Soul and reference files for both LLM roles
│   ├── soul_planner.md          # Planner identity, rules, and escalation logic
│   ├── soul_worker.md           # Worker identity, execution discipline, verification rules
│   └── core_ref.md              # Shared reference injected into both system prompts
│
├── providers/                   # One file per LLM provider, loaded dynamically
│   ├── __init__.py              # Dynamic loader (load_provider)
│   ├── base.py                  # Abstract BaseAgent — provider contract
│   ├── claude.py                # Anthropic Claude
│   └── openai.py                # OpenAI GPT
│
├── core/
│   ├── __init__.py
│   ├── xml_parser.py            # Parse think/plan/work/action tags from AI responses
│   ├── context_window.py        # Scored page stack with automatic eviction
│   ├── prompt_evaluator.py      # Proactive context retrieval + skill/mod hinting
│   └── log.py                   # Unified logger singleton
│
├── engine/                      # Agentic execution layer
│   ├── __init__.py
│   ├── state.py                 # LangGraph AgentState TypedDict
│   ├── nodes.py                 # Planner, actor, reflector, replanner + media builder
│   ├── graph.py                 # LangGraph StateGraph assembly + compilation
│   ├── loop.py                  # Session wrapper — owns context windows + graph
│   ├── sandbox.py               # Shell execution backend (local or Docker)
│   ├── mod_api.py               # ModResult + memory API for all mods
│   ├── media.py                 # MediaAttachment dataclass + provider serialization
│   ├── frame_server.py          # Generic live frame HTTP server
│   ├── server.py                # FastAPI HTTP server — task queue + SSE streaming
│   ├── scheduler.py             # Polling loop for scheduled tasks
│   └── plan_manager.py          # Plan file read/write/advance API
│
├── memory/                      # Persistent memory layer (SQLite + ChromaDB + flat file)
│   ├── __init__.py
│   ├── db.py                    # SQLite setup, schema, row helpers
│   ├── memory.py                # Flat file + ChromaDB dual-write
│   ├── embedder.py              # Ollama embeddings → ChromaDB vector store
│   ├── rag.py                   # Semantic retriever over ChromaDB
│   ├── long_term.py             # Key-value preferences (never expires)
│   ├── vault.py                 # Bucketed knowledge vault (ChromaDB + .md files)
│   ├── agent.db                 # SQLite database (auto-created)
│   ├── plans/                   # Per-task plan files + index.json (auto-created)
│   ├── chroma/                  # ChromaDB vector store (auto-created)
│   └── logs/                    # Per-session turn transcripts (auto-created)
│
├── mcp_servers/                 # Built-in MCP tool definitions (in-process)
│   ├── __init__.py              # Registers all built-in tools into a FastMCP server
│   ├── shell_tools.py           # run_shell, read_file, write_file
│   ├── memory_tools.py          # memory command
│   ├── web_tools.py             # search_web command
│   ├── ui_tools.py              # debug_ui command
│   ├── schedule_tools.py        # schedule command
│   ├── passwd_tools.py          # passwd command
│   └── vault_tools.py           # vault command
│
├── mods/                        # Drop-in command modules (intercepted shell commands)
│   ├── __init__.py              # Package init — mod handlers (dispatched via MCPRouter)
│   ├── _shared.py               # Shared arg parsing utilities for all mods
│   ├── memory/
│   │   └── memory.py            # memory command — query/read/write/prefs/blobs
│   ├── web_search/
│   │   ├── web_search.py        # search_web command — search/fetch URLs
│   │   └── web_search_tool.py   # Search engine — fetch, parse, chunk, score
│   ├── debug_ui/
│   │   ├── debug_ui.py          # debug_ui command — headless GUI interaction
│   │   └── viewer.py            # Live viewer app (run separately by user)
│   ├── schedule/
│   │   └── schedule.py          # schedule command — cron/interval/once task scheduling
│   ├── vault/
│   │   └── vault.py             # vault command — bucketed knowledge base
│   └── passwd/
│       └── passwd.py            # passwd command — credential manager with <<NAME>> interpolation
│
├── scheduled/                   # JSON files for scheduled tasks (auto-created)
│
├── reactive/                    # Incoming event sources (drop-in subprocess pattern)
│   └── __init__.py
│
└── workspace/                   # AI's working directory (bind-mounted into Docker)
    └── vault/                   # Bucketed knowledge vault (agent-navigable, Obsidian-readable)
        ├── index.json           # Bucket manifest — maintained by the agent
        └── <bucket>/            # One folder per bucket, each an isolated RAG collection
            └── <content>.md
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

# 6. Override provider (and optionally model)
PLANNER_PROVIDER=claude CLAUDE_MODEL=claude-sonnet-4-6 WORKER_PROVIDER=openai ./start.sh

# 7. Override HTTP server address
SERVER_HOST=0.0.0.0 SERVER_PORT=9000 ./start.sh
```

---

## What `start.sh` Does

`start.sh` is the single entry point for all launch scenarios. It runs these steps in order:

1. **Environment** — loads `.env` if present; warns if missing and copies from `.env.example`.
2. **Virtual environment** — creates `.venv/` if it does not exist.
3. **Dependencies** — runs `pip install -r requirements.txt` only when `requirements.txt` has changed (hash check).
4. **Docker sandbox** *(only when `SANDBOX=docker`)* — checks Docker is installed, builds the sandbox image if needed (or rebuilds when `Dockerfile` changes), starts the container with the correct bind mount.
   - With `PROJECT=<path>`: mounts that directory as `/workspace`.
   - Without `PROJECT`: mounts `./workspace/` as `/workspace`.
5. **Agent** — `exec python main.py`, which starts the HTTP server, waits for `/health`, spawns the scheduler subprocess, discovers and starts any reactive processes in `reactive/`, then enters the interactive REPL.

After launch the agent is reachable two ways:
- **REPL** — type messages directly in the terminal; output is streamed back in real time.
- **HTTP** — `POST /queue` to submit a task; `GET /stream/<task_id>` to stream its output.


[[overview]]
