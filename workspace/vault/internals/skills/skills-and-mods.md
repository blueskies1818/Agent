# Skills & Mods Ecosystem

The agent extends its capabilities through two complementary mechanisms: **skills** (Markdown reference cards loaded on demand) and **tools** (MCP servers that intercept shell commands). They operate at different layers and serve different purposes.

---

## Two-Layer Architecture

```
Agent output
     │
     ▼
┌──────────────────────────────────────────────────────┐
│ Tools layer (engine/nodes.py → MCPRouter)            │
│                                                      │
│  command text ──→ MCPRouter.try_handle()             │
│       matched? ──→ MCP tool called (JSON-RPC)        │
│    not matched? ──→ run_command() (sandbox fallback) │
└──────────────────────────────────────────────────────┘
     │
     ▼
┌──────────────────────────────────────────────────────┐
│ Skills layer (files in skills/)                      │
│                                                      │
│  agent loads a .md file on demand                    │
│  PromptEvaluator hints at relevant skills            │
└──────────────────────────────────────────────────────┘
```

**Skills** are passive — Markdown files the agent reads to learn how to do something. **Tools** are active — MCP servers that intercept specific command names and execute code before any shell command runs.

> For full MCP protocol details, external server configuration, and Claude Code integration see [docs/mcp.md](mcp.md).

---

## Skills

### File format

Each skill is a `.md` file in `workspace/vault/internals/skills/` with a YAML frontmatter block:

```markdown
---
name:        ffmpeg
description: Convert and process video and audio files
tags:        video, audio, convert, encode, trim, gif
tier:        global
status:      active
created_at:  2026-04-11
author:      user
uses:        0
---

# FFmpeg — video and audio processing

Use ffmpeg to convert, trim, encode, and extract frames from media files.

## Commands

### Convert MP4 to GIF
```bash
ffmpeg -i input.mp4 output.gif
```
...
```

### Frontmatter fields

| Field | Required | Description |
|-------|---------|-------------|
| `name` | Yes | Unique identifier, matches the filename stem |
| `description` | Yes | One-line description used for semantic search |
| `tags` | Recommended | Comma-separated keywords for keyword matching |
| `tier` | No | `global` (always available) or `project` (workspace-specific) |
| `status` | No | `active` \| `pending` |
| `created_at` | No | ISO date string |
| `author` | No | `user` or `agent` |
| `agent_created` | Optional | `true` if the agent authored this skill |

### Auto-discovery and runtime index

At session start, `engine/loop.py` builds a keyword index over all `.md` files in `SKILLS_DIR`.

`SkillRetriever` keeps an embedded ChromaDB collection of all skills for **semantic hinting**: before each turn, `PromptEvaluator` queries the collection with the user's message and injects the top matching skill names into the planner context as passive hints.

### Loading a skill

The agent loads full skill content via:

```xml
<action type="skill"><n>ffmpeg</n></action>
```

The engine reads `skills/ffmpeg.md` and returns the content to the agent.

---

## Tools (MCP)

### How it works

All tool dispatch goes through `MCPRouter` (`engine/mcp_router.py`). When the agent emits a shell action the flow is:

```
<action type="shell"><command>search_web -query "best pizza"</command></action>
                              │
            engine/nodes.py: _run_shell(command)
                              │
                MCPRouter.try_handle("search_web -query ...")
                 1. parse_command() → name="search_web", args={query: "best pizza"}
                 2. registry lookup → found
                 3. client.call_tool("search_web", {args: "-query \"best pizza\""})
                 4. return (True, ModResult)
                              │
                    if not found → run_command() (sandbox)
```

The agent output format does not change — it still writes shell commands. The MCP layer is an invisible upgrade to the dispatch mechanism.

### Built-in tools

Built-in tools live in `mcp_servers/` and run **in-process** (no subprocess overhead). Each wraps the corresponding `mods/` handler so all existing logic is preserved.

| Command name | Tool file | Description |
|---|---|---|
| `memory` | `mcp_servers/memory_tools.py` | Query, read, write persistent memory |
| `search_web` | `mcp_servers/web_tools.py` | Web search and URL fetch |
| `debug_ui` | `mcp_servers/ui_tools.py` | Headless GUI automation |
| `schedule` | `mcp_servers/schedule_tools.py` | Create / list / cancel scheduled tasks |
| `passwd` | `mcp_servers/passwd_tools.py` | Session-scoped credential cache |
| `vault` | `mcp_servers/vault_tools.py` | Bucketed knowledge vault — write, delete, query |
| `run_shell` | `mcp_servers/shell_tools.py` | Direct sandbox shell (plus read_file, write_file) |

### Tool args format

Each built-in tool accepts a single `args: str` parameter containing the raw CLI flags the agent wrote after the command name. The tool passes this directly to the underlying `handle()` function.

External tools define their own structured JSON Schema — `MCPRouter` uses `parse_command()` from `engine/cli_parser.py` to convert CLI flags to a matching dict automatically.

---

## Built-in commands

### `memory` — query/read/write memory

```bash
memory -query "search terms"       # semantic + keyword search across all stores
memory -read                       # list recent long-term memory entries
memory -write "fact to remember"   # persist a fact
memory -prefs                      # list all long-term preferences
memory -pref key value             # set a preference
memory -blobs                      # list recent task blobs
memory -blobs tags=sqlite,config   # filter blobs by tag
memory -blob blob_name             # load full blob content
memory -sessions                   # list past sessions (newest first)
memory -sessions 5                 # list last 5 sessions
memory -session <session_id>       # load full conversation for a past session
```

### `search_web` — web search

```bash
search_web -query "python async generators"
search_web -query "python async generators" -sources 5
search_web -url "https://docs.python.org/3/"
```

### `debug_ui` — headless GUI interaction (Docker only)

```bash
debug_ui -start "python app.py"    # launch app in virtual display
debug_ui -screenshot               # capture screen
debug_ui -click 640 380            # click at coordinates
debug_ui -type "hello world"       # type text
debug_ui -key Return               # send a key
debug_ui -scroll up                # scroll
debug_ui -close                    # kill app and stop display
```

### `passwd` — session-scoped credential manager

`passwd` is dispatched via the MCP tool layer (`mcp_servers/passwd_tools.py`). Credentials are stored in Python process RAM and **never** written to disk, logs, or embeddings.

```bash
passwd -set GITHUB_TOKEN ghp_xxxx   # store credential in RAM
passwd -load                        # load from .passwd file
passwd -list                        # show stored names (never values)
passwd -clear GITHUB_TOKEN          # remove one credential
```

Credentials are stored **in RAM only**. Use `<<NAME>>` placeholders in commands; the framework substitutes before execution and scrubs values from all output.

### `vault` — bucketed knowledge base

The vault organizes knowledge into isolated named buckets. Each bucket is an independent ChromaDB RAG collection + a folder of `.md` files in `workspace/vault/`. The folder structure mirrors Obsidian's hierarchy; `index.json` maps bucket names to their folder paths, decoupling flat ChromaDB collection IDs from nested folder layouts.

```bash
vault -create  python-async                              # create a bucket
vault -write   python-async generators "body text..."   # write content + re-index immediately
vault -delete  python-async generators                   # remove content from disk + index
vault -query   python-async "how does yield work"        # semantic search within one bucket
vault -query   * "async patterns"                        # semantic search across all buckets
vault -list                                              # list all buckets
vault -contents python-async                             # list content entries in a bucket
vault -reindex python-async                              # re-embed all entries from disk
```

**Reindexing policy:** `vault -write` re-indexes the single file automatically — no manual reindex needed for normal writes. Call `-reindex` explicitly only after manual Obsidian edits, organizer agent file moves, or `path` changes in `index.json`. Full-bucket reindex costs one Ollama embedding call per file (~20-100ms each).

**Navigation without the tool:**
```bash
cat workspace/vault/index.json             # see all buckets + paths
ls  workspace/vault/python/async/          # browse a nested bucket
cat workspace/vault/python/async/generators.md  # read content directly
```

`index.json` is maintained by the **agent**, not the vault module — the agent writes it directly using the standard file skills whenever it creates or reorganizes buckets.

---

## Sandbox access for mods

Mods can run shell commands in the sandbox and save durable data using the `engine/mod_api` functions:

```python
from engine.mod_api import log_action, save_fact, save_pref, get_pref, recall
from engine.sandbox import run_command

output = run_command("ls -la /workspace")
log_action("clicked submit button", source="my_tool")
save_fact("user's project uses FastAPI")
save_pref("editor", "neovim")
results: list[str] = recall("PyQt6 setup", top_k=5)
```

---

## Adding a new built-in tool

1. Add a `register_tools(mcp)` function to an existing or new file in `mcp_servers/`:

```python
# mcp_servers/my_tools.py
def register_tools(mcp) -> None:
    @mcp.tool
    def my_tool(args: str = "") -> str:
        """Does something useful. Args: -do <thing>"""
        from mods.my_tool.my_tool import handle
        import shlex
        parsed = shlex.split(args) if args else []
        return handle(parsed, f"my_tool {args}")
```

2. Register it in `mcp_servers/__init__.py`:

```python
from mcp_servers.my_tools import register_tools as reg_my
reg_my(mcp)
```

3. Optionally create a `mods/my_tool/my_tool.py` handler and a `skills/my_tool.md` reference card.

The tool is available immediately on next startup — no further registration needed.

## Adding a new skill

1. Create `workspace/vault/internals/skills/<name>.md` with a valid frontmatter block.
2. Restart the agent — the keyword index and ChromaDB collection are rebuilt automatically.

The agent can also create skills autonomously by writing the `.md` file directly into `SKILLS_DIR`.

[[overview]]

---

## Connections (graph wiring)

### Medium-connectivity hubs
- [[internals/architecture]]
- [[internals/core]]
- [[internals/memory]]
- [[internals/mcp]]
- [[internals/configuration]]
- [[overview]]

### Skill cards (kept lightweight)
- [[internals/skill-read]]
- [[internals/skill-write]]
- [[internals/skill-edit]]
- [[internals/skill-delete]]
- [[internals/skill-memory]]
- [[internals/skill-web-search]]
- [[internals/skill-debug-ui]]
- [[internals/skill-forge]]
- [[internals/skill-vault]]
- [[internals/skill-passwd]]
