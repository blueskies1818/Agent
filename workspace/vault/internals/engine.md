# Engine Layer

The `engine/` package is the central execution layer. It owns the LangGraph state machine, the two-agent session loop, the sandbox abstraction, the media pipeline, and the live frame server.

---

## `engine/state.py` — `AgentState`

`AgentState` is the `TypedDict` that flows through every node. LangGraph merges partial node return values into this shared state.

```python
class AgentState(TypedDict):
    messages:     Annotated[list[dict], _add_messages]  # message history (append-only)
    plan:         list[str]      # step texts extracted from the planner's <plan> tag
    plan_step:    int            # current 0-indexed step pointer
    actor_turn:   int            # how many times the actor has run this message
    done:         bool           # actor set this → reflector routes to END
    blocked:      bool           # actor escalated → reflector routes to replanner
    escalation:   dict | None    # {level, reason, need} from escalate action
    system:       str            # planner system prompt (rebuilt each message)
    last_actions: list[str]      # formatted action results from the last actor run
```

`messages` uses the `_add_messages` reducer: node returns are **appended**, not replaced. All other fields are overwritten by the most recent node update.

---

## `engine/nodes.py` — Node functions

Four node functions implement the graph. All are pure functions with no global state — agents, context windows, and soul strings are closed over at graph construction time via `functools.partial`.

### `planner(state, agent, ctx, soul, core_ref)`

Runs the planner agent. Flow:

1. Appends a `planning_prompt` to `state["messages"]` directing the agent to think and plan.
2. Enters a **skill-discovery loop** (max 3 iterations):
   - Calls the planner LLM.
   - Parses `<action type="skill" op="search">` or `<action type="skill" op="request_creation">` tags.
   - If found, executes them (keyword search over `SKILLS_DIR`) and appends results, then loops.
   - If no skill actions appear, breaks immediately.
3. Extracts steps from the first `<plan>` block.
4. Executes any `<action type="plan" op="write">` to persist the plan file via `PlanManager`.
5. Returns `{"messages": [...], "plan": steps, "plan_step": 0}`.

### `actor(state, agent, worker_ctx, soul, core_ref, loaded_skills)`

Runs the worker agent for the current plan step. Flow:

1. **Resets** `worker_ctx` and re-seeds it with:
   - `PlanManager.generate_project_log()` — compact `[DONE]` / `[CURRENT]` progress
   - `pm.current_step_text()` — the instruction for this step
   - RAG hits from `MemoryRetriever` scoped to the current step text
2. Builds the worker system prompt from `soul_worker.md` + `core_ref.md` + project log + step.
3. On the first actor turn (`actor_turn == 0`), appends an `execute_prompt` with the execution rules.
4. Strips all but the most recent image from message history — the worker always sees at most one screenshot (the latest). Planner and replanner strip all attachments entirely.
5. Calls the worker LLM. Parses the response.
6. **Escalation check** — if an `<action type="escalate">` is found, returns `blocked=True` with the escalation dict.
7. **Implicit done** — if the response has no actions at all, returns `done=True`.
8. Dispatches all work actions via `_execute_action()`.
9. **Auto-verify writes** — if a `printf` / `cat >` / `tee` command ran and no `cat <filename>` followed, automatically runs `cat <filename>` and appends the result.
10. Embeds the exchange into ChromaDB via `embed_conversation_turn`.
11. Returns updated `messages`, `actor_turn`, `done`, `blocked`, `escalation`, `last_actions`.

### `reflector(state)` + `should_continue(state)`

`reflector` is a no-op node (returns `{}`). All routing logic is in `should_continue`, the conditional edge function:

| Condition | Route |
|-----------|-------|
| `blocked=True` and `escalation.level == "user"` | `"end"` |
| `blocked=True` and `escalation.level == "planner"` | `"replanner"` |
| `done=True` | `"end"` |
| `GRAPH_TURN_LIMIT` reached | `"end"` |
| otherwise | `"actor"` |

### `replanner(state, agent, ctx, soul, core_ref)`

Handles worker escalations. Flow:

1. Builds a `replanner_prompt` describing the block (reason, need, current plan, progress).
2. Calls the planner LLM with two options: inject a step, or escalate to the user.
3. **User escalation** — if the planner emits `<action type="escalate" level="user">`: marks the plan as `paused`, prints the question, returns `done=True, blocked=True`.
4. **Step injection** — if the planner emits `<action type="plan" op="inject_step">`: calls `PlanManager.inject_step()` and returns `blocked=False`, which sends control back to the actor.

### Action dispatch (`_execute_action`)

| `action.type` | What happens |
|---------------|-------------|
| `shell` | Runs through `_run_shell()` — `passwd` interpolation, then `MCPRouter.try_handle()`, then `run_command()` if no tool matched |
| `skill` (op=load) | Reads `<SKILLS_DIR>/<name>.md` and returns the content |
| `memory` | `op=write` → `write_memory(content)`, `op=read` → `read_memory()` |
| `plan` | Delegates to `_handle_plan_action()` → `PlanManager` |
| `done` | Returns `is_done=True`; optional message in `data["message"]` |
| `escalate` | Not dispatched here — handled at node level |

### Multimodal message builder

After all actions run, any `MediaAttachment` objects returned by mods are passed to `engine/media.py`'s `build_message()`. This produces a provider-specific message dict (text + image blocks) that is appended to `state["messages"]` for the next LLM call.

---

## `engine/graph.py` — StateGraph assembly

`build_graph()` wires the four nodes together and returns a compiled LangGraph. Both agents and both context windows are closed over so the graph is session-scoped with no global state.

```
START → planner → actor ←─────────────┐
                     ↓                 │
                 reflector             │
                     │                 │
          ┌──────────┼──────────┐      │
          ↓          ↓          ↓      │
        end       actor      replanner─┘
```

Edge summary:
- `planner → actor` (always)
- `actor → reflector` (always)
- `reflector →` conditional via `should_continue`
- `replanner → actor` (always)

---

## `engine/loop.py` — Session wrapper

`AgentLoop` owns everything for the lifetime of one interactive session.

### Initialisation

```python
loop = AgentLoop()
```

On `__init__`:
- Loads planner and worker agents via `load_provider()`
- Loads `soul_planner.md`, `soul_worker.md`, `core_ref.md` from disk
- Creates `planner_ctx` (24 000 tokens, per-session) and `worker_ctx` (8 000 tokens, reset per node)
- Creates `MemoryRetriever`, `SkillRetriever`, `PromptEvaluator`
- Calls `build_graph(...)` to compile the LangGraph
- Seeds `planner_ctx` with the current sandbox path

### Per-turn flow (`loop.run(user_input)`)

1. `planner_ctx.tick()` — decay recency scores
2. Refresh the `system` source page with the current sandbox path
3. Push `user_input` as a `user` page (relevance 0.90)
4. Run `PromptEvaluator.evaluate(user_input)` → push RAG and skill hint pages
5. Build the planner system prompt from `planner_ctx`
6. `graph.invoke(initial_state)` — runs the full planner → actor → reflector loop
7. Push `last_actions` results into `planner_ctx` (relevance 0.75)
8. Extract the assistant summary and push it into `planner_ctx` (relevance 0.80)
9. `embed_conversation_turn(user, summary)` → ChromaDB

### Eviction handler

When any page is evicted from either context window, `_on_evict(page)` runs. Pages from `memory`, `skill`, or `user` sources with `relevance_score >= EVICTION_SAVE_THRESHOLD` (0.65) are saved to long-term memory via `save_fact()`. Raw shell output (`agent`) and sandbox state (`system`) are excluded.

---

## `engine/sandbox.py` — Shell execution backend

Abstracts where shell commands run. Imports from here are transparent in both modes.

```python
from engine.sandbox import run_command, pull_file, push_file, read_file, is_docker
```

### `run_command(command, timeout=None) → str`

The primary entry point for all shell execution. Routes to the local or Docker backend based on `SANDBOX_MODE`. Returns combined stdout + stderr. Errors are returned as `[ERROR] ...` strings — never raised.

**Local backend:** `subprocess.run(command, shell=True, cwd=SANDBOX_ROOT, ...)`

**Docker backend:** `docker exec -w /workspace agent-sandbox /bin/bash -c "<command>"`

### File transfer

| Function | Direction | Docker | Local |
|----------|-----------|--------|-------|
| `pull_file(container_path, host_path)` | sandbox → host | `docker cp container:path host_path` | `shutil.copy2` within host fs |
| `push_file(host_path, container_path)` | host → sandbox | `docker cp host_path container:path` | `shutil.copy2` within host fs |
| `read_file(container_path) → bytes\|None` | sandbox → memory | `docker exec cat <path>` (binary) | `path.read_bytes()` |

In local mode, "container paths" like `/workspace/foo.txt` are resolved relative to `SANDBOX_ROOT`.

### Status queries

| Function | Returns |
|----------|---------|
| `is_docker()` | `True` if `SANDBOX_MODE == "docker"` |
| `container_running()` | `True` if the Docker container is alive |
| `get_project_display()` | Human-readable label for the current workspace |
| `ensure_sandbox()` | Creates `SANDBOX_ROOT` (local) or starts the container (Docker); call once at startup |

### Container security

The sandbox container runs with a restricted capability set:

```
--cap-drop ALL
--cap-add CHOWN, DAC_OVERRIDE, FOWNER, FSETID, SETGID, SETUID, KILL, NET_BIND_SERVICE
--security-opt no-new-privileges:true
--memory 1g  --cpus 1.0  --pids-limit 512
```

The agent has root access inside the container but cannot touch the host filesystem except for the bind-mounted workspace.

---

## `engine/mod_api.py` — Memory API for mods

`ModResult` is the return type for all mod handlers. The memory functions let mods write to persistent storage without importing from `memory/` directly.

### `ModResult`

```python
@dataclass
class ModResult:
    text:        str
    attachments: list[MediaAttachment] = field(default_factory=list)
```

A mod handler can return either a plain `str` (backward compatible) or a `ModResult`. `MCPRouter` normalizes both to `ModResult` before passing upstream.

### Memory functions

```python
from engine.mod_api import log_action, save_fact, save_pref, get_pref, recall

# Log a lightweight action description to conversation memory.
# Survives context window eviction because it's a short text string.
log_action("clicked submit button at (640, 380)", source="debug_ui")

# Save a durable fact across sessions — written to SQLite + ChromaDB.
save_fact("user's project uses FastAPI")

# Save / read a permanent key-value preference.
save_pref("editor", "neovim")
val = get_pref("editor")

# Semantic search across all memory stores.
results: list[str] = recall("PyQt6 setup", top_k=5)
```

**Why `log_action` instead of returning text:** Screenshots and large command outputs are expensive to keep in context. `log_action` persists a tiny text description instead. The description survives eviction while the raw data does not.

---

## `engine/media.py` — Media pipeline

All mod-produced attachments pass through this pipeline before reaching the LLM.

### `MediaAttachment`

```python
@dataclass
class MediaAttachment:
    type:      Literal["image", "audio", "video", "file"]
    data:      bytes | None = None   # raw bytes; engine does not re-read from disk
    path:      str | None = None     # file path; engine reads during validate
    mime_type: str | None = None     # optional hint; engine detects from magic bytes first
    metadata:  dict = field(default_factory=dict)
```

### Pipeline steps

`process(attachment, provider) → dict | None`

1. **Validate** — checks file exists / bytes non-empty / length ≥ 8 bytes
2. **Normalize** — detects MIME from magic bytes (`PNG`, `JPEG`, `WebP`, `MP3`, `WAV`, `MP4`); for `video/mp4`, attempts to extract the first frame via `ffmpeg` and returns it as `image/png`
3. **Capability check** — verifies the provider supports the MIME type (from `PROVIDERS[provider]["media_caps"]`)
4. **Serialize** — builds a provider-specific content block:

```python
# Anthropic format
{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "<b64>"}}

# OpenAI format
{"type": "image_url", "image_url": {"url": "data:image/png;base64,<b64>", "detail": "low"}}
```

Returns `None` if any step fails — the text portion of the message still reaches the LLM.

### `build_message(text, attachments, provider) → dict`

Assembles the full LLM message dict:

```python
# No attachments — plain text message
{"role": "user", "content": text}

# With attachments — multipart content list
{"role": "user", "content": [{"type": "text", "text": text}, <image_block>, ...]}
```

### Image context policy — max 1 image

Two functions manage attachment lifetime in message history:

- **`strip_attachments_from_history(messages)`** — removes all image/audio blocks from every message. Used by the **planner** and **replanner** which don't need visual state.
- **`strip_all_but_last_image(messages)`** — strips all images except the most recent one. Used by the **actor** so the worker always sees current UI state but never accumulates stale screenshots.

Screenshots are also captured at a reduced resolution (960×600, `-quality 85`) to keep image token costs manageable. The combined effect: at most one screenshot in context at any time, and that screenshot is small enough that its token cost is predictable.

---

## `engine/frame_server.py` — Live frame HTTP server

A lightweight HTTP server that serves the latest frame from any registered capture source. Not specific to any mod — any code that produces images can register a capture function.

### Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | Status page with embedded MJPEG stream |
| `GET /frame` | Latest PNG snapshot (single frame) |
| `GET /stream` | MJPEG stream at ~15 FPS |

Open `http://localhost:9222` in a browser or run `python mods/debug_ui/viewer.py`.

### API

```python
from engine.frame_server import register_source, unregister_source, is_serving

# Register a capture function — auto-starts the server on first registration.
# The function should return PNG bytes or None.
register_source(my_capture_fn)

# Unregister and auto-stop the server when no sources remain.
unregister_source()

is_serving()   # True if the server is running
```

Only one capture source at a time. Calling `register_source` again replaces the previous source. The server runs in a daemon thread — it dies with the process.

### Design

The frame server is completely generic. The `debug_ui` mod registers its screenshot function when a display starts and unregisters it when the display is closed. Any other mod can do the same — the viewer does not know about `debug_ui` specifically. The port is `FRAME_SERVER_PORT` (default 9222).

---

## `engine/context_state.py` — Context snapshot register

In-memory store that lets the running server expose context window state without coupling `loop.py` to `server.py`.

`AgentLoop.run()` calls `write_snapshot(...)` at the end of every turn:

```python
write_snapshot(
    planner_ctx,
    worker_ctx,
    planner_injected={"soul": ..., "core_ref": ..., "sandbox": ..., "mod_index": ...},
    worker_injected={...},
    messages_stats=_compute_messages_stats(final_state["messages"]),
)
```

`messages_stats` carries the accumulated `state["messages"]` token breakdown:

```python
{
    "message_count":    int,   # total messages in state
    "image_count":      int,   # messages with image blocks
    "image_tokens_est": int,   # estimated API tokens for images
    "text_tokens_est":  int,   # estimated tokens for text content
    "total_tokens_est": int,   # combined estimate
}
```

`GET /debug/context` on the HTTP server calls `read_snapshot()` to serve this data. `context_map.py` polls this endpoint and renders it as a live terminal display — planner and worker context windows in full, plus the messages stats row with image token cost highlighted in red when it exceeds budget thresholds.


[[overview]]
