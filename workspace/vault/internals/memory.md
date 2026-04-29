# Memory System

The agent's memory is split across three stores: **SQLite** (authoritative structured records), **ChromaDB** (semantic vector search), and **per-session JSON logs** (human-readable transcripts). All three live under `memory/`. The single Python module that touches the database is `memory/db.py` — every other module works through it.

---

## `memory/db.py` — SQLite backend

`db.py` is the only file that imports `sqlite3` directly. Everything above it works with plain Python dicts.

### Initialisation

```python
from memory.db import init_db

conn = init_db()          # opens/creates agent.db, runs schema, returns connection
conn = init_db(db_path)   # override path (useful in tests)
```

`init_db` sets three PRAGMAs before returning:

| PRAGMA | Value | Why |
|--------|-------|-----|
| `journal_mode` | `WAL` | Safe for concurrent reads while writing |
| `busy_timeout` | `5000` | Wait up to 5 s on a locked DB instead of crashing |
| `foreign_keys` | `ON` | Enforce FK constraints |

### Tables

| Table | Purpose | Expires? |
|-------|---------|---------|
| `long_term` | User preferences, behavioral overrides, project list | Never |
| `sessions` | One row per chat instance; tracks open/closed state | Never |
| `task_blobs` | Full detail record of every completed complex task | Never |
| `blob_index` | Searchable metadata for all blobs (no content) | Never |
| `node_messages` | Inter-node messages within a task | Consumed once |
| `tasks` | Task lifecycle record — one row per user request | Never |
| `conversation` | Rolling conversational memory (turns, summaries, compressions) | Compressed |
| `skill_log` | Internal skill execution audit | Never surfaced |
| `queue_tasks` | HTTP queue task records | On completion |

### Row helpers

All helpers accept and return plain dicts. Callers never touch `sqlite3.Row` directly.

```python
from memory.db import init_db, insert, fetch_one, fetch_all, update, delete

conn = init_db()

insert(conn, "long_term", {"key": "editor", "value": "neovim"})
row  = fetch_one(conn, "long_term", {"key": "editor"})
rows = fetch_all(conn, "long_term", {}, order_by="updated_at DESC")
update(conn, "long_term", {"value": "vim"}, {"key": "editor"})
delete(conn, "long_term", {"key": "editor"})
```

`update()` and `delete()` require a non-empty `where` dict — the helpers refuse full-table operations.

---

## `memory/memory.py` — Dual-write facts and session logging

### Persistent memory facts

`write_memory(content)` persists a fact to **both** stores. SQLite is written first; ChromaDB embedding is best-effort — if Ollama is unavailable the fact is still saved and retrievable.

```python
from memory.memory import read_memory, write_memory, clear_memory

write_memory("user prefers dark mode")    # → SQLite long_term + ChromaDB
text = read_memory()                      # → all 'memory:*' entries joined
clear_memory()                            # → wipe memory:* rows + ChromaDB collection
```

Facts are stored in the `long_term` table under keys prefixed with `memory:` (SHA-256 hash of content). Duplicate writes are silently ignored via `INSERT OR IGNORE`.

### `SessionLogger` — per-session JSON transcripts

Each session optionally produces a file in `memory/logs/<session_id>.json`.

**Ghost session behaviour:** no file is created at construction. The first `log()` call anchors the session to disk. Purely conversational sessions that close without any log call leave no file behind.

```json
{
  "session_id": "2026-04-07_14-32-01",
  "started_at": "2026-04-07T14:32:01Z",
  "ended_at":   null,
  "turns": [
    {"turn": 1, "timestamp": "...", "role": "user", "content": "...", "metadata": {}}
  ]
}
```

All content is scrubbed for credential values via `mods/passwd/cache.scrub()` before writing.

---

## `memory/embedder.py` — Embedding pipeline

Generates embeddings via **Ollama** (local, no API key) and stores them in a persistent ChromaDB collection.

### Prerequisites

```bash
ollama serve
ollama pull nomic-embed-text
```

### Collections

| Collection | Used for |
|------------|---------|
| `agent_memory` | Conversation facts, written memories |
| `agent_skills` | Skill file descriptions for semantic retrieval |

### Key functions

```python
from memory.embedder import embed_and_store, embed_conversation_turn, embed_skill

# Store a fact — deduplicates by content hash
doc_id = embed_and_store("user prefers dark mode", metadata={"source": "agent"})

# Store a user+assistant exchange as one document
embed_conversation_turn(user_text, assistant_text)

# Store a skill file for semantic retrieval (upserts by skill name)
embed_skill(name="ffmpeg", description="Video conversion tool", content="...")
```

Deduplication: `embed_and_store` derives a document ID from SHA-256 of the content. If the ID already exists, the write is skipped.

ChromaDB data lives in `memory/chroma/`. It runs fully embedded in-process — no server needed. If the directory is corrupted it can be wiped and rebuilt from SQLite.

---

## `memory/rag.py` — Semantic retriever

### `MemoryRetriever`

Satisfies the `core.prompt_evaluator.Retriever` protocol. Retrieves the most semantically relevant stored facts for a query.

```python
from memory.rag import MemoryRetriever

retriever = MemoryRetriever(min_score=0.4)
results = retriever.retrieve("how do I write Python files?", top_k=10)
# → list of (content, score) tuples, sorted by score descending
```

**Token-budget retrieval (V2):** Instead of returning a fixed count, `retrieve()` greedily adds results until the next one would push the running token count over `RAG_TOKEN_BUDGET`. This prevents a single retrieval from flooding the context window regardless of document length.

ChromaDB returns distances (`0` = identical). The retriever converts: `score = 1 − distance`.

| Config | Default | Description |
|--------|---------|-------------|
| `RAG_CANDIDATE_K` | `10` | Candidates pulled from ChromaDB |
| `RAG_TOKEN_BUDGET` | `≈ 5 280` | Max tokens added per retrieval (22% of planner context) |
| `RAG_MIN_SCORE` | `0.4` | Results below this cosine similarity are dropped |

### `SkillRetriever`

Semantic search over the `agent_skills` ChromaDB collection. Used for Phase 1 passive skill hints — returns `(name, description, score)` tuples, never full skill content.

```python
from memory.rag import SkillRetriever

retriever = SkillRetriever(min_score=0.0)
hints = retriever.retrieve_hints("convert video to gif", top_k=5)
# → list of (name, description, score) tuples
```

**Bootstrap:** on first use, if the skills collection is empty, `SkillRetriever` indexes all `.md` files in `SKILLS_DIR` automatically. New skills placed in `SKILLS_DIR` are picked up on the next session start when the collection is rebuilt.

---

## `memory/long_term.py` — Permanent key-value preferences

Never expires, never gets summarized away. Injected into every agent phase as a compact key-value block.

```python
from memory.long_term import get, set, delete, get_all, format_for_injection

set(conn, "user_name", "Alice")
name = get(conn, "user_name")          # "Alice"
rows = get_all(conn)                   # [{"key": ..., "value": ..., "updated_at": ...}]
delete(conn, "user_name")              # True if removed

# Build the injection block
block = format_for_injection(conn)
# "user_name: Alice\ntimezone: America/New_York"
```

`set()` uses `INSERT ... ON CONFLICT DO UPDATE` so callers never need to check existence first.

The memory mod writes to this table via `save_pref()` in `engine/mod_api.py`. The eviction handler in `engine/loop.py` also writes here when high-relevance pages are displaced from the context window.

---

## `memory/task_blobs.py` — Task detail blobs

One blob per completed complex task — the permanent raw record of exactly what happened. The agent never loads blobs automatically; it sees only metadata from `blob_index` and calls `read_blob` on demand.

```python
from memory.task_blobs import write_blob, read_blob, query_index, format_for_injection

# Write a blob when a complex task completes
blob_id = write_blob(conn,
    task_id="...", session_id="...",
    name="build_config_system",
    summary="Built the master config system from scratch",
    tags="config,setup,sqlite",
    content="# Full markdown record of the task...",
    date="2026-04-11",
)

# Load by name
content = read_blob(conn, "build_config_system")    # markdown str or None

# Search the index — returns metadata only, never content
hits = query_index(conn, tags="config,sqlite")
hits = query_index(conn, keyword="refactor", days_back=14)
hits = query_index(conn, date="today")

# Build the recent-blob block for context injection
block = format_for_injection(conn, days_back=7)
# "build_config — Built the master config system [2026-04-11]"
```

`query_index()` filters are composable. Default window: last 7 days when no filter is given.

---

## `memory/vault.py` — Bucketed knowledge vault

The vault is a self-organizing knowledge base where the agent explicitly manages named **buckets** — isolated topic silos, each with its own ChromaDB collection and a folder of `.md` files.

### Why buckets instead of one flat store

`agent_memory` is a single collection — all facts compete for relevance on every query. The vault solves this by letting the agent partition knowledge by topic. A query inside `python-async` never sees noise from `docker-networking`.

### Two-layer design

| Layer | Location | Purpose |
|-------|----------|---------|
| `.md` files | `workspace/vault/<bucket>/` | Human-readable, Obsidian-browsable, navigable via shell |
| ChromaDB collection | `memory/chroma/vault:<bucket>` | Semantic search (vectors stay internal) |

Both layers are always in sync — every `vault -write` writes the file **and** re-embeds atomically.

### Workspace placement and shell navigation

The vault lives inside the workspace (`workspace/vault/`) so the agent can navigate it directly with plain shell commands — no tool required:

```bash
cat workspace/vault/index.json              # see all buckets + content counts
ls  workspace/vault/python-async/           # list content in a bucket
cat workspace/vault/python-async/generators.md  # read content directly
```

The agent can also open `workspace/vault/` as an Obsidian vault for human browsing — all content is plain Markdown.

### `index.json` — bucket manifest

`workspace/vault/index.json` is maintained by the **agent**, not the vault module. When the agent creates or removes a bucket it writes the index directly using the standard `write` skill — the same way it edits any workspace file. This keeps the Python layer thin: it only handles what requires ChromaDB.

```json
{
  "updated_at": "2026-04-21T14:32:00",
  "buckets": {
    "python-async": {
      "path": "python-async",
      "created_at": "2026-04-21T14:30:00",
      "content_count": 2
    },
    "project-auth": {
      "path": "project-auth",
      "created_at": "2026-04-21T14:31:00",
      "content_count": 1
    }
  }
}
```

### Key functions

```python
from memory.vault import (
    create_bucket, write_content, read_content, delete_content,
    query_bucket, query_all, list_buckets, list_contents, reindex_bucket,
)

create_bucket("python-async")
write_content("python-async", "generators", "Python generators use yield...")
body = read_content("python-async", "generators")

# Semantic search within one bucket
hits = query_bucket("python-async", "how does yield work")
# → list of (content_name, body, score) tuples

# Semantic search across all buckets
hits = query_all("async patterns")
# → list of (bucket, content_name, body, score) tuples

delete_content("python-async", "generators")
reindex_bucket("python-async")   # re-embed after manual Obsidian edits
```

### Agent navigation workflow

```
1. cat workspace/vault/index.json       → discover what buckets exist
2. vault -query python-async "yield"    → semantic search (ChromaDB)
3. cat workspace/vault/python-async/generators.md  → read full content
4. vault -write python-async new-topic "body..."   → write + re-index
```

---

## Memory Store Comparison

| Store | What it holds | When written | How to query |
|-------|--------------|-------------|-------------|
| `long_term` (SQLite) | User preferences, project list, behavioral overrides | On demand via `memory -pref` or eviction | `long_term.get()`, `memory -prefs` |
| `memory:*` rows (SQLite) | Persistent facts (SHA-hashed, deduplicated) | `write_memory()`, `memory -write` | `read_memory()`, `memory -read` |
| `agent_memory` (ChromaDB) | Same facts, embedded for semantic search | Alongside every `write_memory()` call | `MemoryRetriever.retrieve()`, `memory -query` |
| `agent_skills` (ChromaDB) | Skill descriptions for Phase 1 hinting | On skill registration or bootstrap | `SkillRetriever.retrieve_hints()` |
| `vault:<bucket>` (ChromaDB) | Per-bucket knowledge, isolated by topic | `vault -write` / `write_content()` | `vault -query`, `query_bucket()`, `query_all()` |
| `workspace/vault/` (files) | Human-readable `.md` mirror of vault ChromaDB | Alongside every vault write | Shell (`cat`, `ls`), Obsidian, `vault -read` |
| `task_blobs` (SQLite) | Full detail records of complex tasks | On task completion by the engine | `read_blob()`, `memory -blob <name>` |
| `blob_index` (SQLite) | Blob metadata (name, summary, tags, date) | Alongside every `write_blob()` call | `query_index()`, `memory -blobs` |
| `conversation` (SQLite) | Turn history by session — every user + assistant turn | Every `AgentLoop.run()` call | `memory -session <id>`, `load_session_turns()` |
| `sessions` (SQLite) | Session registry — start time, end time, summary | Session open/close in `AgentLoop` | `memory -sessions`, `list_sessions()` |
| `workspace/sessions/*.md` | Full conversation Markdown — one file per session | Session close (`close_session()`) | `memory -session <id>`, Obsidian, shell |
| `logs/*.json` | Human-readable session transcripts | First log call in a session | File system only |

---

## Session history (`memory/sessions.py`)

Every session is recorded end-to-end and saved to the vault so any past session can be resumed.

### Lifecycle

```
AgentLoop.__init__()
  └─ open_session(session_id)          # inserts row into sessions table

AgentLoop.run(user_input)
  ├─ log_turn(session_id, "user", ...)     # appends to conversation table
  └─ log_turn(session_id, "assistant", ...)

AgentLoop.close()
  └─ close_session(session_id, summary)
       ├─ UPDATE sessions SET ended_at, summary
       ├─ SELECT all conversation turns
       └─ vault.write_content("sessions", session_id, markdown)
              → workspace/sessions/<session_id>.md  (disk, always)
              → ChromaDB vault:sessions collection         (if Ollama available)
```

### Vault entry format

```markdown
---
session_id: 2026-04-28_14-32-01
started_at: 2026-04-28T14:32:01
ended_at:   2026-04-28T15:01:22
turns:      8
---

# Session 2026-04-28 14:32:01

## Summary
Fixed the auth middleware token storage bug.

## Conversation

**User** [2026-04-28 14:32:05]:
Can you fix the auth bug?

**Assistant** [2026-04-28 14:33:10]:
Found the issue in middleware.py line 42...
```

### Resuming a past session

```bash
memory -sessions                      # list recent sessions
memory -sessions 5                    # last 5 only
memory -session 2026-04-28_14-32-01   # load full conversation

# Or use semantic search to find a session by topic:
vault -query sessions "auth bug fix"
memory -vault sessions "auth bug fix"
```

The loaded content goes into context — the agent can then pick up from where it left off.

---

## `wipe_All.py` — Selective data wipe

```bash
python wipe_All.py                  # memory + logs + vectors (default)
python wipe_All.py all              # everything including workspace
python wipe_All.py logs             # session transcripts only
python wipe_All.py memory vectors   # facts + ChromaDB only
python wipe_All.py all --yes        # skip confirmation prompt
```

| Target | What gets wiped |
|--------|----------------|
| `memory` | `memory/memory.txt` (legacy flat file, if present) |
| `logs` | `memory/logs/*.log` session transcript files |
| `vectors` | `memory/chroma/` ChromaDB directory (recreated empty) |
| `workspace` | `workspace/*` (all agent sandbox files) |
| `all` | All four targets above |

The SQLite database (`agent.db`) is not wiped by any target — it is the authoritative record and should be deleted manually if a full reset is needed.


[[overview]]
