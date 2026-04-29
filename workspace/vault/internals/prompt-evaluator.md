# Prompt Evaluator

`core/prompt_evaluator.py` — Proactive context retrieval from user prompts.

## Overview

Before the agent runs, this evaluates each user message and surfaces relevant pages to pre-load into the context window.

- Skill matching is **semantic** (via `SkillRetriever`) instead of keyword-based
- `SkillRetriever` returns hints (name + description only) capped by `SKILL_TOKEN_BUDGET`
- `core_ref.md` is never injected here — it is always-injected by the loop

## Protocols

### `Retriever`
Runtime-checkable protocol for RAG retrieval.

```python
def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]: ...
```

### `SkillHintRetriever`
Runtime-checkable protocol for semantic skill matching. Returns lightweight hints (name + description), not full skill bodies.

## Flow

1. User message arrives
2. `PromptEvaluator` calls `SkillHintRetriever.retrieve(query)` → top-k skill hints
3. Calls `Retriever.retrieve(query)` → top-k memory/RAG snippets
4. Pushes results as pages into the context window before the turn runs

## Connections

- [[internals/core]]
- [[internals/context-window]]
- [[internals/memory]]
- [[internals/engine]]

---

[[overview]]
