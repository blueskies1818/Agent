# Skills & Mods Ecosystem

This document explains how the agent's extensibility system works — skills,
mods, and how they interact.

---

## Two layers of extensibility

```
┌──────────────────────────────────────────────────────┐
│                   Agent (nodes.py)                   │
│                                                      │
│  <action type="shell">                               │
│       │                                              │
│       ▼                                              │
│  ┌──────────────┐    match?     ┌─────────────────┐  │
│  │  ModRouter   │ ───────────→  │  mod handler    │  │
│  │  (mods/)     │    yes        │  (in-process)   │  │
│  └──────┬───────┘               └─────────────────┘  │
│         │ no match                                   │
│         ▼                                            │
│  ┌──────────────┐                                    │
│  │  subprocess  │  →  real shell (bash)              │
│  └──────────────┘                                    │
│                                                      │
│  <action type="skill">                               │
│       │                                              │
│       ▼                                              │
│  Load skills/*.md into context window                │
└──────────────────────────────────────────────────────┘
```

### Skills (`skills/`)

Skills are **documentation files** — `.md` files that teach the agent how to
do something. They are loaded on demand into the context window when the
agent requests them.

- **Location:** `skills/`
- **Format:** Markdown with YAML frontmatter + command examples
- **Loaded via:** `<action type="skill"><n>skill_name</n></action>`
- **Effect:** Injected into context window as a scored page
- **Auto-discovery:** Drop a `.md` file in `skills/` — it appears automatically
- **Runtime index:** `engine/loop.py:_skill_index()` scans all skill files at startup
  and injects a compact 1-liner index into every system prompt

Each skill file should begin with a `description:` frontmatter field:

```markdown
---
description: One-line summary shown in the runtime skill index.
---
```

This is what the agent sees before deciding which skill to load. Keep it concise.

Skills don't execute anything. They're reference material the agent reads
before acting.

### Mods (`mods/`)

Mods are **executable command handlers** — self-contained Python packages that
intercept shell commands before they hit `subprocess`. They look like shell
commands to the agent but run in-process with full access to the codebase.

- **Location:** `mods/<mod_name>/`
- **Format:** Subdirectory with a handler `.py` file + any internal deps
- **Invoked via:** `<action type="shell"><command>mod_name -flag "args"</command></action>`
- **Effect:** Command is intercepted, handler runs, output returned to agent
- **Auto-discovery:** The ModRouter scans all subdirectories of `mods/` at startup

```
mods/
├── __init__.py              ← ModRouter (scans subdirs)
├── memory/
│   └── memory.py            ← NAME = "memory", handle()
└── web_search/
    ├── web_search.py         ← NAME = "search_web", handle()
    └── web_search_tool.py    ← internal dep (fetcher, parser, scorer)
```

Each mod is a self-contained package. Internal dependencies (like the web
search engine) live alongside the handler in the same directory — they are
never imported from the project root.

---

## How skills and mods work together

Most mods have a matching skill file. The skill teaches the agent *how* to
use the mod. The mod *does* the work.

```
skills/memory.md              ←  teaches the agent the memory command syntax
mods/memory/memory.py         ←  handles `memory -query "..."` when the agent calls it

skills/web_search.md          ←  teaches the agent the search_web command syntax
mods/web_search/web_search.py ←  handles `search_web -query "..."` when the agent calls it
```

The flow:
1. User says "do you remember what we built last week?"
2. `prompt_evaluator.py` keyword-matches "remember" → hints that the `memory` skill exists
3. Agent requests `<action type="skill"><n>memory</n></action>`
4. Agent reads the skill definition and learns the `memory -query` syntax
5. Agent emits `<action type="shell"><command>memory -query "last week"</command></action>`
6. `_run_shell()` in `nodes.py` checks the ModRouter first
7. ModRouter matches `memory` → dispatches to `mods/memory/memory.handle()`
8. Handler queries SQLite, ChromaDB, flat file → returns results
9. Agent sees results and summarises for the user

---

## Adding a new mod

### Step 1: Create the mod package

```
mods/
└── my_tool/
    ├── my_tool.py          ← handler (required)
    └── helper_lib.py       ← internal dependency (optional)
```

### Step 2: Write the handler

Create `mods/my_tool/my_tool.py`:

```python
"""
mods/my_tool/my_tool.py — Short description.
"""

NAME        = "my_tool"
DESCRIPTION = "One-line description shown in the system prompt"


def handle(args: list[str], raw: str) -> str:
    """
    Called when the agent runs: my_tool [args...]

    Args:
        args: Tokenized arguments after the command name.
              e.g. for "my_tool -query foo" → ["-query", "foo"]
        raw:  The full raw command string, useful for extracting
              quoted arguments.

    Returns:
        A string that will be shown to the agent as the command output.
    """
    if not args:
        return "Usage: my_tool -action <value>"

    flag = args[0].lower().lstrip("-")

    if flag == "ping":
        return "pong"

    return f"Unknown flag: {flag}"
```

Internal dependencies are imported from the same package:

```python
# In mods/my_tool/my_tool.py
from mods.my_tool.helper_lib import some_function
```

### Step 3: Create the matching skill

Create `skills/my_tool.md`. The `description:` frontmatter field is required — it's
the 1-liner shown in the runtime skill index injected into every system prompt:

```markdown
---
description: One-line summary shown in the runtime skill index.
---

# My Tool — short description

Use shell commands to interact with My Tool.

## Commands

### Ping
\```
my_tool -ping
\```

## When to use
- When the user asks about X or Y
```

The index is built at runtime by `engine/loop.py:_skill_index()`, which scans
`skills/*.md`, reads the `description:` frontmatter field, and injects a compact
table into the system prompt. No registration needed — drop the file, restart, it appears.

### Step 4: Add keyword triggers (optional)

In `core/prompt_evaluator.py`, add an entry to `_SKILL_KEYWORDS`:

```python
_SKILL_KEYWORDS = {
    # ... existing entries ...
    "my_tool": [
        "my_tool", "related", "keywords", "that", "trigger", "the", "hint",
    ],
}
```

This makes the agent aware the skill exists before it even asks for it.

### Step 5: Done

No imports to update, no registration code. Restart the agent and the
ModRouter picks up the new package automatically.

---

## Mod interface contract

Every mod handler file must define these at module level:

| Attribute     | Type               | Description                              |
|---------------|--------------------|------------------------------------------|
| `NAME`        | `str`              | Command name (first token matched)       |
| `DESCRIPTION` | `str`              | One-liner for the system prompt index    |
| `handle`      | `(list, str) → str`| Execute the command and return output    |

### `handle(args, raw)` details

- `args`: List of string tokens after the command name, split by whitespace
- `raw`: The full original command string (for extracting quoted arguments)
- **Return:** A string — this becomes the `[SHELL RESULT]` the agent sees
- **Errors:** Return strings starting with `[ERROR]` for failures
- **Never raise:** Catch all exceptions internally and return error strings

---

## Sandbox access for mods

Mods run on the host, but they can execute commands inside the Docker
container and transfer files in/out.  Import from `engine.sandbox`:

```python
from engine.sandbox import run_command, pull_file, push_file, read_file, is_docker
```

### Available functions

| Function | Signature | Description |
|----------|-----------|-------------|
| `run_command` | `(command, timeout?) → str` | Execute a shell command inside the sandbox |
| `pull_file` | `(container_path, host_path) → bool` | Copy a file FROM the sandbox TO the host |
| `push_file` | `(host_path, container_path) → bool` | Copy a file FROM the host INTO the sandbox |
| `read_file` | `(container_path) → bytes \| None` | Read file contents as bytes (e.g. screenshots) |
| `is_docker` | `() → bool` | Check if running in Docker mode |

All functions work transparently in both local and docker mode.  In local
mode, `run_command` uses subprocess and file transfers are plain copies.

### Example: a mod that captures a screenshot inside the container

```python
from engine.sandbox import run_command, read_file, is_docker
import base64

def _capture_screenshot() -> bytes | None:
    """Take a screenshot of the virtual display inside the container."""
    # Run the capture command inside the sandbox
    run_command("import -window root /tmp/screenshot.png")

    # Read the image bytes back to the host
    return read_file("/tmp/screenshot.png")

def _send_to_llm(image_bytes: bytes) -> str:
    b64 = base64.b64encode(image_bytes).decode()
    # ... send to vision model ...
```

### When to use each function

- **`run_command()`** — Use when your mod needs to run a program inside the
  container (xdotool, python scripts, build tools).  This is the same
  function that powers all shell actions.

- **`read_file()`** — Use when you need binary file contents (screenshots,
  compiled outputs) without saving to a temp file on the host.

- **`pull_file()` / `push_file()`** — Use when you need files to persist on
  the host (logs, artifacts) or need to inject host files into the container
  (configs, test data).

- **Direct imports** — Mods that only need host-side resources (SQLite,
  ChromaDB, network APIs) don't need sandbox functions at all.  Just import
  what you need from the codebase directly, like `memory_mod` does.

---

## Built-in mods

### `memory` (`mods/memory/memory.py`)

Queries, reads, and writes persistent memory across sessions.

| Command                              | Effect                              |
|--------------------------------------|-------------------------------------|
| `memory -query "search terms"`       | Search all memory stores            |
| `memory -read`                       | Read flat memory.txt                |
| `memory -write "fact to persist"`    | Write to memory.txt + ChromaDB     |
| `memory -prefs`                      | List long-term preferences          |
| `memory -pref key value`             | Set a preference                    |
| `memory -blobs`                      | List recent task blobs              |
| `memory -blobs tags=X`              | Filter blobs by tag                 |
| `memory -blob name`                  | Load full blob content              |

The `-query` operation searches across all stores at once: preferences,
blob index, conversation history, ChromaDB embeddings, and flat file.

### `search_web` (`mods/web_search/`)

Searches the internet using DuckDuckGo and returns relevant text excerpts.
The search engine (`web_search_tool.py`) is bundled in the same package.

| Command                                   | Effect                         |
|-------------------------------------------|--------------------------------|
| `search_web -query "search terms"`        | Search (3 sources by default)  |
| `search_web -query "terms" -sources 5`    | Search with more sources       |
| `search_web -url "https://example.com"`   | Fetch and parse a specific URL |

---

## Architecture decisions

**Why shell-style commands instead of new XML action types?**

- The agent already knows how to emit shell actions — no new syntax to learn
- Mod commands are visually indistinguishable from shell commands in the agent's output
- The interception is transparent: if a mod isn't loaded, the command falls
  through to the real shell (where it will fail with "command not found")
- Adding a mod never requires changing `xml_parser.py` or `state.py`

**Why subdirectory packages instead of flat files?**

- Each mod is fully self-contained — handler + dependencies live together
- Internal dependencies (like `web_search_tool.py`) don't pollute the project root
- Easy to copy, move, or share a mod as a single folder
- Clear boundary between "this is the handler" and "these are its internal helpers"

**Why separate mods/ from skills/?**

- Skills are documentation (read-only, injected into context)
- Mods are code (executable, produce side effects)
- Keeping them separate makes it clear what's information vs. capability
- A skill can exist without a mod (pure documentation, e.g. `read.md`)
- A mod can exist without a skill (if usage is obvious from the system prompt)
- But most mods should have a skill file — the agent does better with examples

**Why dynamic loading?**

- Drop-in extensibility: add a folder, restart, it works
- No central registry to maintain
- Each mod is self-contained — easy to test in isolation