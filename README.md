# AI Shell Agent

An autonomous AI agent with direct shell access to an isolated Docker sandbox. The agent reasons, plans, and executes structured actions — running commands, managing files, searching the web, querying persistent memory, and interacting with GUI applications through a headless virtual display.

---

## Why I Built This

The reason I wanted to create a project like this one was to gain a deeper understanding of "agentic Operating systems" AI is one of the fields I am passionate about that means learning how some of the leading tools functions. More specifically I wanted to understand how a relatively static LLM can interact with the real world, plan long term actions and how information is stored in the frameworks "long term memory" then called efficiently when that information is needed.


## Some Challenges

Some challenges I faced while co-authoring this project with an AI assistant were with memory and sorting relevant information, how you can allow the agent to grow and develop through skill additions by the agent itself, and getting the in-process streaming of a headless display to work inside a Docker container. I'm sure more will come up as I continue to make this agent framework better.

## How did I overcome these challenges and what did I learn

### sorting relivent information for storage
The main issue was that the agent’s limited memory would fill up with outdated logs and large files, pushing out important items like spesific instructions or task paramiters. To fix this, I implemented a system that can be thaought of like a person with a backpack full of pages and book that that the user can add to or the ai can add to, the ai can only have inmemory how many pages it can hold and it prunes or stores relivent data to its backback (the rag database), this system evaluates data based on relevance and recency. Instead of deleting the oldest information, the system evicts the least useful data. I also adopted a "see once, remember briefly" rule: the agent views large screenshots to reason about them, but then replaces them in memory with tiny text descriptions (e.g., "clicked submit button"). This keeps the context lean while retaining the essential history of actions. (this was spesificly for debug ui mod and you can add a diffrent form of impelmentation if you want)


### Agent Development via Skill atonomis skill additions

I wanted the agent to improve without manual coding. I created a two layer system: Skills (Markdown files for instructions) and Mods (Python packages for execution). By routing all mod commands through a standard shell system, the agent can use new tools by finding them online or building them its self. This architecture allows the agent or a user to add more funtions simply by creating a mod file in a new folder, with out affecting the rest of the system


### In-prosses streaming inside docker container

Running a visual interface inside Docker was complex due to process and security restrictions. I solved this by using setsid to prevent processes from closing prematurely and disabling Firefox's internal sandbox, as the Docker container already provides security. I also standardized screenshot formats and implemented a blank detection retry loop to account for slow loading apps. Finally, I built a dedicated frame server so users can watch the agent’s actions in real time (spesificcly implemented in debug ui but like I said earlyer you can add diffrent funtionality like letting the user interact thought the same interface with apps in the docker container). The takeaway I got of of it was that while Docker changes how code behaves, a strong abstraction layer can make the container environment feel invisible to the agent.


---

## How It Works

The agent runs as an interactive REPL. Each message triggers a three-node LangGraph state machine:

```
User message
     │
     ▼
 ┌─────────┐   think + plan
 │ planner │ — displays [plan] to user
 └────┬────┘
      │
 ┌────▼────┐   execute actions, display results
 │  actor  │ ◄──────────────────────────────────┐
 └────┬────┘                                    │
      │                                         │
 ┌────▼──────┐  done or turn limit?             │
 │ reflector │ → yes → END                      │
 └───────────┘ → no  ─────────────────────────── ┘
```

The agent communicates through structured XML tags:

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
├── main.py                  — Interactive REPL entry point
├── config.py                — All settings (providers, paths, limits)
├── soul.md                  — Agent identity, personality, and rules
│
├── providers/               — LLM backends (Claude, OpenAI)
├── agents/                  — BaseAgent interface
│
├── engine/                  — Agentic execution layer
│   ├── loop.py              — Session wrapper, owns context window + graph
│   ├── graph.py             — LangGraph state machine assembly
│   ├── nodes.py             — Planner, actor, reflector implementations
│   ├── sandbox.py           — Shell execution (local or Docker)
│   ├── mod_api.py           — Shared API for mods (ModResult, log_action, save_fact)
│   └── frame_server.py      — Live screenshot HTTP server
│
├── core/                    — Reasoning utilities
│   ├── context_window.py    — Scored page stack with automatic eviction
│   ├── prompt_evaluator.py  — Proactive RAG retrieval + skill hinting
│   └── xml_parser.py        — Parses think/plan/work/action tags
│
├── memory/                  — Persistent memory layer
│   ├── db.py                — SQLite schema and helpers
│   ├── memory.py            — Flat file + ChromaDB dual-write
│   ├── embedder.py          — OpenAI embeddings → ChromaDB
│   ├── rag.py               — Semantic retriever
│   ├── long_term.py         — Permanent key-value preferences
│   ├── conversation.py      — Rolling conversation history
│   ├── task_blobs.py        — Completed task records
│   └── sessions.py          — Session lifecycle
│
├── mods/                    — Drop-in command modules
│   ├── memory/              — Query/read/write persistent memory
│   ├── web_search/          — DuckDuckGo search + URL fetch
│   ├── debug_ui/            — Headless GUI interaction + screenshots
│   ├── skill_forge/         — Agent-authored skill registration
│   └── passwd/              — Session-scoped credential cache
│
└── skills/                  — Skill definitions loaded on demand (.md files)
    ├── read.md, write.md, edit.md, delete.md
    ├── memory.md, web_search.md, debug_ui.md
    ├── skill_forge.md, passwd.md
```

---

## Key Features

### Docker Sandbox
Shell commands run inside an isolated container. The agent has root access inside but cannot reach the host filesystem beyond the mounted workspace.

### Persistent Memory
Multi-layered memory that survives across sessions — SQLite for structured data, ChromaDB for semantic search, and a flat file fallback. Every conversation turn is embedded and retrievable.

### Context Window Management
A scored page stack with automatic eviction. Pages are scored by `relevance × 0.6 + recency × 0.4`. High-value pages that get evicted under token pressure are saved to long-term memory before being dropped.

### Self-Authoring Skills
The agent can identify capability gaps, install tools, write skill documentation, and register new skills into its own system using the `skill_forge` mod. New skills are discoverable immediately via frontmatter keywords.

### Credential Management
Session-scoped credential cache with `<<NAME>>` placeholder syntax. Credentials are substituted before execution and scrubbed from all output, context, logs, and embeddings — the LLM only ever sees placeholder names.

### Headless GUI
Full GUI automation via Xvfb + xdotool inside the container. The agent can launch applications, click, type, scroll, and take screenshots. A live viewer streams what the agent sees to `http://localhost:9222`.

---

## Quick Start

```bash
# 1. Clone and enter the directory
cd Agent/

# 2. Copy and fill in API keys
cp .env.example .env

# 3. Launch (local mode — no Docker needed)
./start.sh

# 4. Launch with Docker sandbox
SANDBOX=docker ./start.sh

# 5. Launch with a project directory synced into the sandbox
PROJECT=/home/user/my-app SANDBOX=docker ./start.sh
```

---

## Configuration

All settings live in `config.py`:

| Variable | Default | Description |
|----------|---------|-------------|
| `ACTIVE_PROVIDER` | `"openai"` | LLM provider (`claude`, `openai`) |
| `ACTIVE_TIER` | `"smart"` | Model tier (`fast` or `smart`) |
| `SANDBOX_MODE` | `"local"` | `"local"` or `"docker"` |
| `MAX_CONTEXT_TOKENS` | `8000` | Context window token budget |
| `EVICTION_SAVE_THRESHOLD` | `0.65` | Min relevance score to save a page on eviction |
| `RAG_TOP_K` | `5` | Memory pages retrieved per turn |
| `GRAPH_TURN_LIMIT` | `None` | Max actor cycles per message |
| `SHELL_TIMEOUT` | `30` | Seconds before a shell command is killed |

---

## Extending the Agent

### Adding a Mod
1. Create `mods/my_tool/my_tool.py` — define `NAME`, `DESCRIPTION`, `handle(args, raw)`
2. Create `skills/my_tool.md` — document the command syntax
3. No registration needed — the mod router discovers it automatically

### Agent-authored Skills
The agent can create its own skills at runtime:
```
skill_forge -register my_tool.md my_tool
```
The workspace file is moved into `skills/` and becomes immediately discoverable.

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
