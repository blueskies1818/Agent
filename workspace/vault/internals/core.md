# Core Layer

The `core/` package provides the building blocks used by every other layer. Nothing in `core/` imports from `engine/`, `memory/`, or `mods/` — it is the foundation, not the framework.

---

## `core/xml_parser.py` — Response decomposition

The AI speaks in a mixed format: plain text reasoning mixed with structured XML tags. The parser splits a raw LLM response into five typed parts so the engine can handle each one correctly.

### Tags

| Tag | Visible to user | Fed back to LLM | Purpose |
|-----|----------------|-----------------|---------|
| `<think>...</think>` | Dimmed header only | No | Internal monologue — reasoning the AI does not expose |
| `<plan>...</plan>` | Yes (step list) | No | Step-by-step breakdown shown while planning |
| `<work>...</work>` | Yes (status line) | No | Short "what I'm doing right now" line |
| `<action ...>` | Depends on type | As result | Executable action the engine runs and returns the output of |
| plain text | Yes | As assistant turn | Reasoning / final reply shown to the user |

### Action types

```xml
<!-- Shell command -->
<action type="shell"><command>ls -la</command></action>

<!-- Load a skill -->
<action type="skill"><n>write</n></action>

<!-- Planner: search for a skill -->
<action type="skill"><op>search</op><query>compress video</query></action>

<!-- Planner: request skill creation -->
<action type="skill"><op>request_creation</op><name>ffmpeg</name><reason>...</reason></action>

<!-- Write to memory -->
<action type="memory"><op>write</op><content>fact</content></action>

<!-- Plan operations -->
<action type="plan"><op>write</op><title>Task title</title><steps>1. step one\n2. step two</steps></action>
<action type="plan"><op>step_done</op><step>2</step></action>
<action type="plan"><op>note</op><content>discovery</content></action>
<action type="plan"><op>read</op></action>
<action type="plan"><op>status</op><value>paused</value></action>
<action type="plan"><op>list</op></action>
<action type="plan"><op>resume</op><task_id>2026-04-07_refactor-auth</task_id></action>

<!-- Escalation -->
<action type="escalate"><level>planner</level><reason>...</reason><need>clarification</need></action>
<action type="escalate"><level>user</level><reason>...</reason></action>

<!-- Signal done -->
<action type="done"/>
```

### `parse_response(text)`

```python
reasoning, actions, thinks, plans, works = parse_response(raw_llm_output)
```

Returns:
- `reasoning` — plain text with all tags stripped; shown to the user and stored in message history
- `actions` — `list[Action]` in document order; each has `.type` and `.data` dict
- `thinks` — `list[ThinkBlock]`; not fed back to the LLM
- `plans` — `list[PlanBlock]`; each has `.content` (raw text) and `.steps` (parsed list)
- `works` — `list[WorkBlock]`; short status strings

**Parser robustness:** The action parser tries `xml.etree.ElementTree` first for clean XML. If that fails (common for shell commands containing `>`, `<`, `&`, or heredocs), it falls back to a regex extractor that also HTML-unescapes the content.

### `format_result(action, output)`

Wraps an action result for the next LLM turn:

```
[SHELL RESULT]
<output text>
[/SHELL RESULT]
```

---

## `core/context_window.py` — Scored page stack

The context window is a priority queue of `Page` objects injected into the system prompt. When the token budget is exceeded, the lowest-scored page is evicted first — not necessarily the oldest one.

### Page

```python
@dataclass
class Page:
    content:         str
    source:          Source   # "system" | "agent" | "memory" | "skill" | "user"
    relevance_score: float    # 0.0–1.0, fixed at creation time
    turn_added:      int      # which turn the page was pushed
    tokens:          int      # approximate token count (len(text) // 4)
```

### Score formula

```
score = relevance_score × RELEVANCE_WEIGHT + recency × RECENCY_WEIGHT

recency = 1.0 / (1.0 + age × 0.2)
age     = current_turn − turn_added
```

A page added this turn has `recency = 1.0`. A page added 5 turns ago has `recency ≈ 0.50`. Default weights: `RELEVANCE_WEIGHT = 0.6`, `RECENCY_WEIGHT = 0.4`.

### Sources and typical scores

| Source | Typical relevance | Description |
|--------|------------------|-------------|
| `system` | 1.0 | Sandbox state, turn counter — injected every turn |
| `user` | 0.90 | Current user message |
| `agent` | 0.75–0.80 | Shell output, agent summaries |
| `memory` | RAG score | Facts retrieved from ChromaDB or flat file |
| `skill` | RAG score | Skill hint injected by PromptEvaluator |

### `ContextWindow` API

```python
ctx = ContextWindow(
    max_tokens=24_000,
    relevance_weight=0.6,
    recency_weight=0.4,
    on_evict=callback,      # optional; called just before a page is dropped
)

ctx.tick()                  # advance turn counter — call once per agentic iteration
ctx.push(content, source, relevance_score)   # add a page; evicts if over budget
ctx.render()                # formatted string for system prompt injection
ctx.score(page)             # compute current score for a page
ctx.clear_source("system")  # remove all pages from a source
ctx.clear()                 # remove all pages (used to reset worker context)

used, total = ctx.token_usage
count = ctx.page_count
turn  = ctx.current_turn
```

### Eviction callback

When a page is evicted under token pressure, `on_evict(page)` is called synchronously before the page is removed. The session loop uses this to save important pages to long-term memory:

```python
def _on_evict(page: Page) -> None:
    if page.source in {"memory", "skill", "user"}:
        if page.relevance_score >= EVICTION_SAVE_THRESHOLD:  # default 0.65
            save_fact(page.content)
```

`agent` and `system` sources are excluded — raw shell output and sandbox state are not worth persisting.

### Credential scrubbing

Before a page is stored, `push()` runs the `passwd` mod's `scrub()` function on the content. This strips any active credential values that were interpolated via `<<NAME>>` placeholders so they never accumulate in context.

### Render order

`render()` sorts pages from **lowest to highest score** so the most relevant content sits closest to the end of the system prompt, where LLMs attend most strongly.

---

## `core/prompt_evaluator.py` — Proactive context retrieval

Before each turn, `PromptEvaluator.evaluate(user_input)` runs two retrievers and returns a list of `RetrievedPage` objects ready to push into the context window.

### What it retrieves

1. **Memory** — calls `rag.retrieve(user_input, top_k=RAG_CANDIDATE_K)` on the `MemoryRetriever`. Results with `score >= RAG_MIN_SCORE` become `source="memory"` pages.

2. **Skill hints** — calls `skill_rag.retrieve_hints(user_input, top_k=3)` on the `SkillRetriever`. Hits become `source="skill"` pages with content:
   ```
   Skill available: 'write' — Create files using printf
   Request it with: <action type="skill"><n>write</n></action>
   ```
   Only the name and description are injected — not the full skill file. The agent loads the full definition on demand with a skill action.

### Token budgets

The loop enforces separate token caps for each retriever's output via `RAG_TOKEN_BUDGET` and `SKILL_TOKEN_BUDGET` (see [configuration.md](configuration.md)).

### Retriever protocols

`PromptEvaluator` accepts any object that satisfies the `Retriever` or `SkillHintRetriever` protocols — making it easy to swap out retrieval backends:

```python
class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]: ...

class SkillHintRetriever(Protocol):
    def retrieve_hints(self, query: str, top_k: int = 5) -> list[tuple[str, str, float]]: ...
    #                                                                 ↑name  ↑desc  ↑score
```

### `core_ref.md`

`core_ref.md` is a shared reference document (tool formats, action syntax) always injected into both the planner and worker system prompts by the session loop. It is **not** handled by `PromptEvaluator` — it is loaded once at startup and passed directly into the system prompt builder.

---

## `core/log.py` — Unified logger singleton

A zero-dependency logging singleton. Import it from anywhere with no setup:

```python
from core.log import log

log.info("sandbox ready", source="sandbox")
log.error("tool failed to load", source="mcp_router")
log.fatal("provider not found — cannot continue")   # logs then raises SystemExit(1)
```

### Output format

```
[14:32:01] [INFO]  [sandbox] Container started
[14:32:05] [ERROR] [mcp_router] Failed to load mcp_servers/debug_ui
[14:32:09] [FATAL] Provider not found — cannot continue
```

### Methods

| Method | Level | Stream | Colour | Raises |
|--------|-------|--------|--------|--------|
| `log.info(msg, source="")` | INFO | stdout | dim white | No |
| `log.error(msg, source="")` | ERROR | stderr | yellow | No |
| `log.fatal(msg, source="")` | FATAL | stderr | bold red | `SystemExit(1)` |

`source` is an optional tag shown in square brackets. Use the module name or subsystem name (e.g. `"sandbox"`, `"loop"`, `"plan_manager"`).


[[overview]]


---

## Connections (graph wiring)

### Foundation links (core should be highly central)
- [[internals/engine]]
- [[internals/context-window]]
- [[internals/prompt-evaluator]]
- [[internals/log]]
- [[internals/memory]]
- [[internals/skills-and-mods]]
- [[internals/mcp]]
- [[internals/sandbox]]
- [[internals/configuration]]
- [[internals/architecture]]

### Action / execution contracts
- [[internals/overview]]
- [[internals/core-ref]]
- [[internals/memory]]

### Two-soul identity
- [[internals/soul-worker]]
- [[internals/soul-planner]]

### Graph / runtime
- [[internals/server-and-scheduler]]
- [[internals/debug-ui]]

### Routing MOC
- [[overview]]
