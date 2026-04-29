# AI Shell Agent

An autonomous AI agent with a planner/worker architecture and direct shell access to an isolated Docker sandbox. A planner reasons about requests and writes structured plans; a worker executes them step by step — running shell commands, managing files, searching the web, querying persistent memory, and interacting with GUI applications through a headless virtual display. A built-in HTTP server and scheduler let external systems submit tasks and receive streamed results.

---

## Why I Built This

The reason I wanted to create a project like this one was to gain a deeper understanding of "agentic operating systems". AI is one of the fields I am passionate about, and that means learning how some of the leading tools function. More specifically I wanted to understand how a relatively static LLM can interact with the real world, plan long-term actions, and how information is stored in the framework's "long term memory" — then called efficiently when that information is needed.


## Some Challenges

Some challenges I faced while co-authoring this project with an AI assistant were with memory and sorting relevant information, how you can allow the agent to grow and develop through skill additions by the agent itself, and getting the in-process streaming of a headless display to work inside a Docker container. I'm sure more will come up as I continue to make this agent framework better.

## How did I overcome these challenges and what did I learn

### Sorting relevant information for storage
The main issue was that the agent's limited memory would fill up with outdated logs and large files, pushing out important items like specific instructions or task parameters. To fix this, I implemented a system that can be thought of like a person with a backpack full of pages and books that the user or the AI can add to. The AI can only hold in-memory as many pages as it can hold, and it prunes or stores relevant data to its backpack (the RAG database). This system evaluates data based on relevance and recency. Instead of deleting the oldest information, the system evicts the least useful data. I also adopted a "see once, remember briefly" rule: the agent views large screenshots to reason about them, but then replaces them in memory with tiny text descriptions (e.g., "clicked submit button"). This keeps the context lean while retaining the essential history of actions. (This was specifically for the debug_ui mod and you can add a different form of implementation if you want.)


### Agent development via autonomous skill additions

I wanted the agent to improve without manual coding. I created a two-layer system: Skills (Markdown files for instructions) and Mods (Python packages for execution). By routing all mod commands through a standard MCP layer, the agent can use new tools by finding them online or building them itself. This architecture allows the agent or a user to add more functions simply by creating a mod file in a new folder, without affecting the rest of the system.


### In-process streaming inside a Docker container

Running a visual interface inside Docker was complex due to process and security restrictions. I solved this by using `setsid` to prevent processes from closing prematurely and disabling Firefox's internal sandbox, as the Docker container already provides security. I also standardized screenshot formats and implemented a blank detection retry loop to account for slow-loading apps. Finally, I built a dedicated frame server so users can watch the agent's actions in real time (specifically implemented in debug_ui but like I said earlier you can add different functionality). The takeaway I got out of it was that while Docker changes how code behaves, a strong abstraction layer can make the container environment feel invisible to the agent.


---

## How It Works

The agent runs as an interactive REPL backed by an HTTP server. Each message triggers a four-node LangGraph state machine with two independent LLMs — one planner and one worker:

```
User message
     │
     ▼
┌──────────┐   think + write plan
│ planner  │──────────────────────────────────┐
└────┬─────┘                                  │
     │                                        │ (trivial / conversational turns:
     ▼                                        │  planner replies directly → END)
┌──────────┐   execute current step           │
│  actor   │◄────────────────────┐            │
└────┬─────┘  (worker LLM)       │            │
     │                           │            │
┌────▼──────┐  done? blocked?    │            │
│ reflector │                    │            │
└─────┬─────┘                    │            │
      │                          │            │
      ├── continue ───────────────┘            │
      │                                       │
      ├── blocked ──→ ┌───────────┐           │
      │               │ replanner │───────────┘
      │               └───────────┘
      │
      └── done ──→ END
```

The worker communicates through structured XML tags:

```xml
<think>Internal reasoning — never shown to the user.</think>
<plan>
  1. Check if the file exists
  2. Write the content
  3. Verify and confirm
</plan>
<work>Writing the file now.</work>
<action type="shell"><command>printf 'hello\n' > file.txt</command></action>
```

---

## Architecture

```
Agent/
├── main.py                      # Entry point — starts server, scheduler, REPL
├── config.py                    # All settings (providers, paths, limits, ports)
├── context_map.py               # Terminal live view of context windows + image token stats
├── wipe_All.py                  # Selective memory / plan / session data wipe utility
├── mcp_config.json              # External MCP server configuration (add third-party tools here)
│
├── agents/                      # Soul + reference files for both LLM roles
│   ├── soul_planner.md          # Planner identity, rules, and escalation logic
│   ├── soul_worker.md           # Worker identity, execution discipline, verification rules
│   └── core_ref.md              # Shared reference injected into both system prompts
│
├── providers/                   # LLM backends (Claude, OpenAI) — loaded dynamically
│   ├── base.py                  # Abstract BaseAgent interface
├── reactive/                    # Incoming message sources (webhooks, sockets, file watchers)
│
├── engine/                      # Agentic execution layer
│   ├── loop.py                  # Session wrapper — owns context windows + graph
│   ├── graph.py                 # LangGraph state machine assembly
│   ├── nodes.py                 # Planner, actor, reflector, replanner
│   ├── state.py                 # LangGraph AgentState TypedDict
│   ├── context_state.py         # In-memory context snapshot register (/debug/context)
│   ├── sandbox.py               # Shell execution backend (local or Docker)
│   ├── mod_api.py               # ModResult + memory API for mods
│   ├── media.py                 # MediaAttachment pipeline — validate, normalize, serialize
│   ├── mcp_router.py            # Routes shell commands to MCP tools or sandbox
│   ├── mcp_client.py            # In-process MCP client
│   ├── cli_parser.py            # Shell flag → structured dict converter for MCP args
│   ├── plan_manager.py          # Plan file read/write/advance/inject API
│   ├── server.py                # FastAPI HTTP server — task queue + SSE streaming
│   ├── scheduler.py             # Polling loop for scheduled tasks
│   └── frame_server.py          # Generic live frame HTTP server (debug_ui viewer)
│
├── core/                        # Reasoning utilities
│   ├── context_window.py        # Scored page stack with automatic eviction
│   ├── prompt_evaluator.py      # Proactive RAG retrieval + skill hinting
│   ├── xml_parser.py            # Parses think/plan/work/action tags
│   └── log.py                   # Unified logger singleton
│
├── memory/                      # Persistent memory layer
│   ├── db.py                    # SQLite schema and helpers
│   ├── memory.py                # Flat file + ChromaDB dual-write
│   ├── embedder.py              # Ollama embeddings → ChromaDB
│   ├── rag.py                   # Semantic retriever (memory + skill)
│   ├── vault.py                 # Bucketed knowledge base backend
│   └── plans/                   # Per-task plan files + index.json (auto-created, gitignored)
│
├── mcp_servers/                 # Built-in MCP tool definitions
│
├── mods/                        # Drop-in command modules (dispatched via MCPRouter)
│   ├── memory/                  # Query/read/write persistent memory
│   ├── web_search/              # DuckDuckGo search + URL fetch
│   ├── debug_ui/                # Headless GUI interaction + screenshots
│   ├── schedule/                # Cron/interval/once task scheduling
│   ├── vault/                   # Bucketed knowledge base (per-topic RAG + .md files)
│   └── passwd/                  # Session-scoped credential cache
│
└── workspace/vault/internals/skills/   # Skill definitions loaded on demand (.md files)
```

---

## Key Features

### Two-Soul Planner / Worker Model
Two independent LLMs with separate identities and context windows. The **planner** reasons about goals and writes structured plans. The **worker** executes one step at a time, verifies results, and escalates to the planner if blocked. Each role uses an independently configurable provider (`PLANNER_PROVIDER`, `WORKER_PROVIDER`) and model (`CLAUDE_MODEL`, `OPENAI_MODEL`). Both default to OpenAI.

### Docker Sandbox
Shell commands run inside an isolated container. The agent has root access inside but cannot reach the host filesystem beyond the mounted workspace.

### Persistent Memory
Multi-layered memory that survives across sessions — SQLite for structured data, ChromaDB for semantic search (via local Ollama embeddings), and per-session JSON transcripts. Every conversation turn is embedded and retrievable.

### Context Window Management
Two independently managed context windows. The planner's window (24 000 tokens) accumulates full session history; the worker's window (8 000 tokens) is reset fresh for each step. Pages are scored by `relevance × 0.6 + recency × 0.4`. High-value pages evicted under token pressure are saved to long-term memory before being dropped.

### HTTP Server + Scheduler
A FastAPI server (`POST /queue`, `GET /stream/<id>`) accepts tasks from any external system and streams typed SSE output. A companion scheduler fires tasks on cron or interval schedules, reading JSON files from `scheduled/`.

### MCP Tool Dispatch
All shell commands pass through `MCPRouter` before reaching the sandbox. Built-in tools (`memory`, `search_web`, `debug_ui`, `schedule`, `vault`, `passwd`) run in-process via MCP. Unmatched commands fall through to the Docker sandbox. External MCP servers (filesystem, GitHub, etc.) can be added to `mcp_config.json` without touching source code.

### Self-Authoring Skills
The agent can identify capability gaps, write skill documentation, and place new `.md` files into `workspace/vault/internals/skills/`. New skills are picked up at session start via the keyword index and ChromaDB collection rebuild.

### Knowledge Vault
Bucketed knowledge base via the `vault` mod. Each bucket is an independent ChromaDB collection backed by `.md` files in `workspace/vault/<bucket>/` — readable in Obsidian and searchable by the agent. Buckets are fully isolated; queries within one bucket never see noise from another.

### Credential Management
Session-scoped credential cache with `<<NAME>>` placeholder syntax. Credentials are stored in RAM only — substituted before execution and scrubbed from all output, context, logs, and embeddings.

### Headless GUI
Full GUI automation via Xvfb + xdotool inside the container. The agent can launch applications, click, type, scroll, and take screenshots. Screenshots are capped at 960×600 and the context always holds at most one image — the most recent — to prevent token budget exhaustion. A live viewer streams what the agent sees to `http://localhost:9222`.

### Context Map
`context_map.py` is a terminal live-view tool that polls the running server and displays planner/worker context windows in full — each page shown in its source color, separated by relevance-percentage dividers. Also tracks accumulated `state["messages"]` image token cost so token budget pressure is visible at a glance.

---

## Quick Start

```bash
# 1. Enter the project directory
cd Agent/

# 2. Copy and fill in API keys
cp .env.example .env

# 3. Launch (local mode — no Docker needed)
./start.sh

# 4. Launch with Docker sandbox
SANDBOX=docker ./start.sh

# 5. Launch with a project directory synced into the sandbox
PROJECT=/home/user/my-app SANDBOX=docker ./start.sh

# 6. Override provider / model per role
PLANNER_PROVIDER=claude CLAUDE_MODEL=claude-sonnet-4-6 WORKER_PROVIDER=openai ./start.sh

# 7. Submit a task via HTTP (server starts automatically)
curl -s -X POST http://127.0.0.1:8765/queue \
  -H "Content-Type: application/json" \
  -d '{"prompt": "list the files in /workspace"}'
```

---

## Configuration

All settings live in `config.py`. Runtime overrides via environment variables:

### Agent roles

| Env var | Default | Description |
|---------|---------|-------------|
| `PLANNER_PROVIDER` | `openai` | LLM provider for the planner (`claude`, `openai`) |
| `WORKER_PROVIDER` | `openai` | LLM provider for the worker |
| `CLAUDE_MODEL` | `claude-sonnet-4-6` | Model used when provider is `claude` |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Model used when provider is `openai` |

### Sandbox & server

| Env var / Variable | Default | Description |
|--------------------|---------|-------------|
| `SANDBOX_MODE` | `"local"` | `"local"` or `"docker"` |
| `SHELL_TIMEOUT` | `30` | Seconds before a shell command is killed |
| `SERVER_HOST` | `127.0.0.1` | FastAPI server bind address |
| `SERVER_PORT` | `8765` | FastAPI server port |
| `FRAME_SERVER_PORT` | `9222` | Live frame viewer port |

### Context window

| Variable | Default | Description |
|----------|---------|-------------|
| `PLANNER_CONTEXT_TOKENS` | `24 000` | Token budget for the planner's per-session context |
| `WORKER_CONTEXT_TOKENS` | `8 000` | Token budget for the worker's per-step context |
| `EVICTION_SAVE_THRESHOLD` | `0.65` | Min relevance score to save a page on eviction |
| `RAG_TOP_K` / `RAG_CANDIDATE_K` | `10` | Memory pages retrieved per turn |
| `GRAPH_TURN_LIMIT` | `None` | Max actor cycles per message; `None` = unlimited |

---

## Extending the Agent

### Adding a built-in tool (MCP)
1. Add a `register_tools(mcp)` function to a file in `mcp_servers/`.
2. Register it in `mcp_servers/__init__.py`.
3. Optionally create `mods/my_tool/my_tool.py` and `skills/my_tool.md`.

### Adding a skill
1. Create `skills/<name>.md` with a valid YAML frontmatter block (`name`, `description`, `tags`).
2. Restart — the keyword index and ChromaDB collection rebuild automatically.

### Agent-authored skills
The agent can create its own skills at runtime by writing a `.md` file with valid YAML frontmatter directly into `workspace/vault/internals/skills/`. The skill becomes available on the next session start (or when the index is rebuilt).

### Scheduled tasks
```bash
curl -s -X POST http://127.0.0.1:8765/schedule \
  -H "Content-Type: application/json" \
  -d '{"task_id": "daily-summary", "prompt": "Summarise workspace activity", "schedule_type": "cron", "schedule_value": "0 9 * * 1-5"}'
```

---

## Roadmap

- [x] LangGraph planner → actor → reflector loop
- [x] Two-soul planner / worker split with independent LLMs and context windows
- [x] Worker escalation path → replanner node
- [x] Docker container sandbox with root access
- [x] Project directory sync (bind mount)
- [x] Persistent memory (SQLite + ChromaDB + flat file)
- [x] Semantic memory retrieval (RAG) injected before each turn
- [x] Scored context window with automatic eviction
- [x] Eviction-triggered memory persistence
- [x] Per-turn conversation embedding (Ollama local embeddings)
- [x] Drop-in mod system via MCP tool dispatch
- [x] External MCP server support (mcp_config.json)
- [x] Web search mod (DuckDuckGo)
- [x] Headless GUI mod (Xvfb + xdotool + screenshots)
- [x] Live frame streaming (viewer + browser)
- [x] Multimodal pipeline — mods return images; max-1-image context policy
- [x] Agent-authored skills (skills written to workspace/vault/internals/skills/)
- [x] Bucketed knowledge vault (vault mod — per-topic RAG + Obsidian .md files)
- [x] Session-scoped credential cache with <<NAME>> interpolation + scrubbing
- [x] FastAPI HTTP server — task queue + SSE streaming
- [x] Scheduled tasks (cron / interval / once)
- [x] Plan files with step tracking and mid-plan injection
- [x] Reactive input sources package (webhook / socket / file watcher)
- [x] Live context map terminal viewer (context_map.py)
- [ ] Auto-distillation — LLM extracts facts from context at turn end
- [ ] Workspace edit history / checkpoints
- [ ] Multi-agent support (agent spawning sub-agents)

