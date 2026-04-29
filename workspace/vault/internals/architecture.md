# Architecture: Planner / Worker Split

The agent is built around a two-soul model. A **planner** reasons about the user's request and produces a structured plan; a **worker** executes that plan one step at a time. Each has its own LLM, context window, and identity file. The LangGraph state machine coordinates them.

---

## The Two-Soul Model

| | Planner | Worker |
|---|---|---|
| Soul file | `soul_planner.md` | `soul_worker.md` |
| Provider config | `AGENTS["planner"]` | `AGENTS["worker"]` |
| Default model | OpenAI (`OPENAI_MODEL`) | OpenAI (`OPENAI_MODEL`) |
| Context window | Per-session, accumulates | Reset each invocation |
| Context tokens | `PLANNER_CONTEXT_TOKENS` (24 000) | `WORKER_CONTEXT_TOKENS` (8 000) |
| Sees | Full session history, RAG, skill hints | Project log, current step, fresh RAG |
| Can emit | Plans, direct replies, escalation to user | Shell actions, memory writes, escalation to planner |

**Planner identity** (`soul_planner.md`): orchestrator. Reasons before committing, writes unambiguous step instructions, handles small conversational turns directly, interprets worker results, escalates to the user only when genuinely blocked.

**Worker identity** (`soul_worker.md`): executor. Runs the assigned step exactly as written, verifies every result, never claims completion before seeing actual output, escalates to the planner (not the user) when blocked.

---

## LangGraph State Machine

```
user message
     │
     ▼
┌──────────┐   think + write plan
│ planner  │──────────────────────────────────────────────┐
└────┬─────┘                                              │
     │                                                    │ (if plan is trivial /
     ▼                                                    │  purely conversational,
┌──────────┐   execute current step, emit actions         │  planner replies directly
│  actor   │◄─────────────────────────────────┐           │  and graph ends here)
└────┬─────┘   (worker runs here)             │           │
     │                                        │           │
     ▼                                        │           │
┌────────────┐  check: done? blocked?         │           │
│ reflector  │                                │           │
└─────┬──────┘                                │           │
      │                                       │           │
      ├── continue (not done, not blocked) ───┘           │
      │                                                   │
      ├── blocked (worker escalation) ──→ ┌────────────┐  │
      │                                   │ replanner  │──┘
      │                                   └────────────┘
      │
      └── done ──→ END
```

### Nodes

| Node | Agent | What it does |
|------|-------|-------------|
| `planner` | Planner | Reads user message + planner context; decides to handle directly or write a plan via `PlanManager.write_plan()` |
| `actor` | Worker | Builds a fresh worker system prompt from the project log + current step; runs one LLM call; dispatches all `<action>` tags |
| `reflector` | — | Pure logic node; inspects state flags (`done`, `blocked`, `escalation`) and returns a routing decision |
| `replanner` | Planner | Receives an escalation from the worker; decides to inject a clarification step, resolve the block, or surface the question to the user |

---

## Planner → Worker Handoff

1. The **planner** calls `PlanManager.write_plan(title, steps)`, which writes a plan file to `memory/plans/<task_id>.md`.
2. The plan is stored in `AgentState["plan"]` (list of step strings) and `AgentState["plan_step"]` (current index).
3. Each **actor** invocation reads `PlanManager.generate_project_log()` (completed steps + `[CURRENT]` label) and the text of the current step.
4. When the worker emits `<action type="done"/>`, the reflector advances `plan_step` and loops back to the actor for the next step.
5. When all steps are done, the reflector routes to `END`.

---

## When the Planner Handles Directly

The planner skips spawning the worker for turns that don't require shell access:

- Greetings, thanks, small talk
- Pure questions answerable from context or memory
- Memory lookups that don't need shell commands

In these cases the planner writes its reply directly into `AgentState["messages"]` and sets `state["done"] = True`. The reflector routes immediately to `END`.

---

## Worker Escalation Path

If the worker cannot complete a step it emits:

```xml
<action type="escalate">
  <level>planner</level>
  <reason>Two config files match the description: config/app.yml and config/prod.yml.</reason>
  <need>clarification</need>
</action>
```

`need` values: `clarification` | `research` | `skill`

The reflector detects `state["blocked"] = True` and routes to the **replanner** node. The replanner either:
- **Injects a clarification step** via `PlanManager.inject_step()` and sends the worker back
- **Surfaces the question** to the user if the block cannot be resolved internally

---

## Plan File Format

Plans are stored as Markdown with YAML frontmatter in `memory/plans/<task_id>.md`.  
When a workspace is active, the plan also lives at `<workspace>/.agent/plan.md`.

```markdown
---
task_id:    2026-04-11_refactor-auth
status:     active
workspace:  /home/user/my-app
created_at: 2026-04-11T14:32:00Z
updated_at: 2026-04-11T15:01:00Z
---

# Refactor auth middleware

## Steps
- [x] Read existing auth code
- [ ] Rewrite token storage   ← CURRENT
- [ ] Write tests
- [ ] Summarise and confirm to the user

## Notes
- Found two token stores; using sessions table only
```

**Frontmatter fields:**

| Field | Description |
|-------|-------------|
| `task_id` | Date-slug identifier, e.g. `2026-04-11_refactor-auth` |
| `status` | `active` \| `paused` \| `complete` \| `failed` |
| `workspace` | Absolute path to the project directory, or empty for local mode |
| `created_at` / `updated_at` | ISO 8601 UTC timestamps |

The `← CURRENT` marker on a step tells the worker which step it is executing. `PlanManager.step_done(n)` advances the marker to the next undone step. `PlanManager.inject_step(after_n, text)` inserts a `[INJECTED]` step mid-plan.

A global index at `memory/plans/index.json` tracks all plans and their statuses.

---

## Context Window Ownership

**Planner context** (`planner_ctx`) is per-session — it accumulates user messages, action results, and agent summaries across the full conversation. Before each turn the prompt evaluator injects RAG memory hits and skill hints into it.

**Worker context** (`worker_ctx`) is reset at the start of every actor node invocation. It is built fresh from the project log, the current step text, and a focused RAG query scoped to that step.

This keeps the planner context large enough to hold session history while keeping the worker context lean and focused on the task at hand.

---

## Session Lifecycle

```
AgentLoop.__init__()
  ├── load planner agent  (load_provider)
  ├── load worker agent   (load_provider)
  ├── load soul_planner.md, soul_worker.md, core_ref.md
  ├── create planner_ctx  (ContextWindow, 24 000 tokens)
  ├── create worker_ctx   (ContextWindow, 8 000 tokens)
  ├── create MemoryRetriever + SkillRetriever
  ├── create PromptEvaluator
  └── build_graph(...)    (LangGraph compiled graph)

AgentLoop.run(user_input)
  ├── planner_ctx.tick()           (decay recency scores)
  ├── planner_ctx.push(user_input)
  ├── evaluator.evaluate(user_input) → inject RAG + skill hints
  ├── build_planner_system_prompt(planner_ctx, ...)
  ├── graph.invoke(initial_state)
  ├── push action results into planner_ctx
  ├── push agent summary into planner_ctx
  └── embed_conversation_turn(user, summary) → ChromaDB
```


[[overview]]


---

## Connections (graph wiring)

### Central nodes (high-connectivity)
- [[internals/overview]]
- [[internals/core]]
- [[internals/core-ref]]
- [[internals/configuration]]
- [[internals/engine]]
- [[internals/memory]]
- [[internals/sandbox]]
- [[internals/providers]]
- [[internals/server-and-scheduler]]
- [[internals/mcp]]
- [[internals/debug-ui]]
- [[internals/skill-debug-ui]]
- [[internals/skills-and-mods]]

### Two-soul architecture
- [[internals/soul-worker]]
- [[internals/soul-planner]]

### Skill ecosystem (light links from core)
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

### MOCs / routing
- [[overview]]

[[skill-read]]
