# Configuration Reference

All settings live in `config.py`. Nothing else in the codebase should hardcode paths, models, or provider names — change values here to reshape the whole system.

---

## Environment Variables (`.env`)

Copy `.env.example` to `.env` and fill in your API keys before first launch.

```env
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

Runtime overrides — set these in the shell or via `start.sh`:

```bash
# Provider selection (per role)
PLANNER_PROVIDER=claude   # default: openai
WORKER_PROVIDER=openai    # default: openai

# Sandbox
SANDBOX=docker            # default: local
PROJECT=/home/user/my-app # default: (none — uses ./workspace/)

# HTTP server
SERVER_HOST=0.0.0.0       # default: 127.0.0.1
SERVER_PORT=9000          # default: 8765
```

---

## Providers

```python
PROVIDERS = {
    "claude": {
        "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        "media_format": "anthropic",
        "media_caps":   ["image/png", "image/jpeg", "image/webp"],
    },
    "openai": {
        "model": os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        "media_format": "openai",
        "media_caps":   ["image/png", "image/jpeg", "image/webp"],
    },
}
```

Each provider has a single `model` string. Override via `CLAUDE_MODEL` or `OPENAI_MODEL` env vars.

To add a provider: drop a file in `providers/`, implement the `BaseAgent` contract, add an entry here.

---

## Agent Roles

Each role is independently configurable and can use a different provider.

```python
AGENTS = {
    "planner": {
        "provider": os.getenv("PLANNER_PROVIDER", "openai"),
    },
    "worker": {
        "provider": os.getenv("WORKER_PROVIDER", "openai"),
    },
}
```

| Key | Env var | Default | Description |
|-----|---------|---------|-------------|
| `planner.provider` | `PLANNER_PROVIDER` | `"openai"` | Provider used for the planner role |
| `worker.provider` | `WORKER_PROVIDER` | `"openai"` | Provider used for the worker role |

The model used is determined by the provider's `model` entry in `PROVIDERS`. To use Claude for the planner, set `PLANNER_PROVIDER=claude` and optionally override the model via `CLAUDE_MODEL`.

---

## Paths

| Variable | Value | Description |
|----------|-------|-------------|
| `BASE_DIR` | `Path(__file__).parent` | Absolute path to the project root; all other paths resolve relative to this |
| `SKILLS_DIR` | `BASE_DIR / "workspace" / "vault" / "internals" / "skills"` | Where skill `.md` files are discovered |
| `MODS_DIR` | `BASE_DIR / "mods"` | Root path for mod packages (used internally by mod handlers) |
| `LOGS_DIR` | `BASE_DIR / "memory" / "logs"` | Per-session turn transcript directory |
| `SCHEDULED_DIR` | `BASE_DIR / "scheduled"` | JSON files for scheduled tasks |
| `VAULT_DIR` | `SANDBOX_ROOT / "vault"` | Bucketed knowledge vault — lives in the workspace so the agent can navigate it via shell. Resolves to `workspace/vault/` by default or `<PROJECT>/vault/` when a project is mounted. |
| `MEMORY["db_path"]` | `BASE_DIR / "memory" / "agent.db"` | SQLite database path |

---

## Project Directory

```python
PROJECT_DIR: str | None = os.getenv("PROJECT", "").strip() or None
```

When set, the agent treats this host directory as the active workspace. In Docker mode it is bind-mounted as `/workspace`. In local mode it becomes `SANDBOX_ROOT`.

---

## Sandbox

| Variable | Default | Description |
|----------|---------|-------------|
| `SANDBOX_MODE` | `"local"` | `"local"` runs commands via `subprocess`; `"docker"` runs them via `docker exec` |
| `SANDBOX_ROOT` | `PROJECT_DIR` or `./workspace/` | Working directory for shell commands in local mode |
| `DOCKER_CONTAINER_NAME` | `"agent-sandbox"` | Name of the Docker container |
| `DOCKER_SHELL` | `"/bin/bash"` | Shell used inside the container |
| `DOCKER_WORKDIR` | `"/workspace"` | Working directory inside the container |

---

## Virtual Display

Used by the `debug_ui` mod for headless GUI interaction inside the container.

| Variable | Default | Description |
|----------|---------|-------------|
| `DISPLAY_RESOLUTION` | `"1280x800x24"` | Xvfb display resolution and colour depth |
| `DISPLAY_NUMBER` | `":99"` | X display number |
| `UI_SETTLE_DELAY` | `1.5` | Seconds to wait after a UI action before taking a screenshot |

---

## Frame Server

The live frame server streams screenshots over HTTP. Any mod can register a capture function.

| Variable | Default | Description |
|----------|---------|-------------|
| `FRAME_SERVER_PORT` | `9222` | Port for the frame server; open in a browser or run `python mods/debug_ui/viewer.py` |

---

## Loop Limits

| Variable | Default | Description |
|----------|---------|-------------|
| `MAX_TURNS` | `30` | Hard stop on total actor loop iterations per session |
| `GRAPH_TURN_LIMIT` | `None` | Max actor cycles per single user message; `None` = unlimited |
| `STREAM` | `True` | Stream LLM output token by token |

---

## Shell

| Variable | Default | Description |
|----------|---------|-------------|
| `SHELL_TIMEOUT` | `30` | Seconds before a shell command is killed |

---

## Context Window

| Variable | Default | Description |
|----------|---------|-------------|
| `PLANNER_CONTEXT_TOKENS` | `24_000` | Token budget for the planner's per-session context |
| `WORKER_CONTEXT_TOKENS` | `8_000` | Token budget for the worker's per-node context (reset each invocation) |
| `MAX_CONTEXT_TOKENS` | `8_000` | Legacy single-context budget (unused in V2 dual-agent mode) |
| `RELEVANCE_WEIGHT` | `0.6` | Weight of semantic relevance in page scoring |
| `RECENCY_WEIGHT` | `0.4` | Weight of recency in page scoring |
| `EVICTION_SAVE_THRESHOLD` | `0.65` | Pages evicted under token pressure with `relevance_score ≥ this` are saved to long-term memory. `agent` and `system` sources are excluded. |

Page score formula: `relevance × RELEVANCE_WEIGHT + recency × RECENCY_WEIGHT`

---

## RAG

| Variable | Default | Description |
|----------|---------|-------------|
| `RAG_CANDIDATE_K` | `10` | Candidates pulled from ChromaDB before budget filtering |
| `RAG_TOKEN_BUDGET` | `≈ 5 280` | Max tokens of RAG results injected into the planner context per turn (22% of `PLANNER_CONTEXT_TOKENS`) |
| `SKILL_TOKEN_BUDGET` | `≈ 2 400` | Separate token cap for skill hint injection (10% of `PLANNER_CONTEXT_TOKENS`) |
| `RAG_MIN_SCORE` | `0.4` | Minimum cosine similarity for a ChromaDB result to be used |

---

## Embeddings

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_EMBED_MODEL` | `"nomic-embed-text"` | Ollama model used to embed conversation turns and memory facts into ChromaDB |

---

## Web Search

| Variable | Default | Description |
|----------|---------|-------------|
| `WEB_SEARCH_SOURCES` | `3` | Default number of pages to fetch per search query |
| `WEB_SEARCH_SEMANTIC` | `False` | Enable semantic re-ranking of search results |

---

## HTTP Server

The HTTP server (`engine/server.py`) is started as a subprocess by `main.py`.

| Variable | Env var | Default | Description |
|----------|---------|---------|-------------|
| `SERVER_HOST` | `SERVER_HOST` | `"127.0.0.1"` | Host the FastAPI server binds to |
| `SERVER_PORT` | `SERVER_PORT` | `8765` | Port the FastAPI server listens on |

Set `SERVER_HOST=0.0.0.0` to accept connections from other machines.

---

## Scheduler

The scheduler (`engine/scheduler.py`) is started as a subprocess by `main.py`. It polls `SCHEDULED_DIR` for JSON task files.

| Variable | Default | Description |
|----------|---------|-------------|
| `SCHEDULED_DIR` | `BASE_DIR / "scheduled"` | Directory scanned for scheduled task JSON files |

---

## Wiping Data

```bash
python wipe_All.py                 # memory + logs + vectors
python wipe_All.py all             # everything including workspace
python wipe_All.py logs            # just session logs
python wipe_All.py memory vectors  # specific targets
python wipe_All.py all --yes       # skip confirmation
```


[[overview]]
