# Context Window

`core/context_window.py` — Scored page stack for context management.

## Overview

Pages are pushed onto the stack from any source (system, agent, memory, skill, user). Each page carries a **relevance score** set at creation time and a **recency score** that decays each turn.

```
final_score = relevance × RELEVANCE_WEIGHT + recency × RECENCY_WEIGHT
```

When a new page would push total token usage over `MAX_CONTEXT_TOKENS`, the page with the **lowest final_score** is evicted — not necessarily the oldest. This keeps high-relevance older pages alive while stale low-relevance pages get dropped first.

## Page Sources

| Source   | Description |
|----------|-------------|
| `system` | Auto-injected each turn (sandbox state, turn counter) |
| `agent`  | Results from shell commands / actions the AI ran |
| `memory` | Facts retrieved from persistent memory / RAG |
| `skill`  | Full skill definitions loaded on demand |
| `user`   | Relevant snippets surfaced from past user messages |

## Key Concepts

- **Eviction policy**: lowest `final_score` evicted first (not LRU)
- **Recency decay**: recency score drops each turn, so old pages drift down unless relevance keeps them up
- **Token budget**: hard cap at `MAX_CONTEXT_TOKENS`; eviction fires before adding any page that would exceed it

## Connections

- [[internals/core]]
- [[internals/prompt-evaluator]]
- [[internals/memory]]
- [[internals/engine]]

---

[[overview]]
