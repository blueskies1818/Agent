---
name:        memory
description: Query, read, and write persistent memory — searches conversation history, preferences, blobs, RAG, and vault buckets
tags:        remember, recall, memory, forget, know, previous session, last time, earlier, preference, vault, knowledge, search
tier:        global
status:      active
created_at:  2026-04-01
author:      user
uses:        0
---

# Memory — persistent memory and vault knowledge search

Use `memory` to query, read, and write anything that should survive across sessions.
The `-query` command searches ALL stores at once — conversation history, preferences,
task blobs, semantic embeddings, and vault knowledge buckets.

---

## Commands

### Search everything (most useful)
```bash
memory -query "what do i know about PyQt6"
```
Searches ALL stores in one call:
- Long-term preferences
- Completed task summaries and blob metadata
- Conversation history (summaries, compressions)
- Semantic embeddings (RAG / ChromaDB)
- Vault knowledge buckets (all registered buckets)

### Search a specific vault bucket
```bash
memory -vault skills "how do I install a CLI tool"
memory -vault internals "context window eviction"
memory -vault * "async generator patterns"
```
`*` searches every bucket in `workspace/vault/index.json`. Results include
the bucket name, doc name, and a preview of the matching content.

### Write a fact to memory
```bash
memory -write "user prefers dark mode in all UIs"
```

### Read the long-term memory store
```bash
memory -read
```

### List long-term preferences
```bash
memory -prefs
```

### Set a preference
```bash
memory -pref timezone America/New_York
memory -pref style "concise, no emoji"
```

### List recent task blobs (completed task records)
```bash
memory -blobs
memory -blobs tags=memory,sqlite
memory -blobs days=14
```

### Load full content of a task blob
```bash
memory -blob build_config_system
```

---

## When to use

| Situation | Command |
|---|---|
| "Do you remember..." or "What do you know about..." | `memory -query` |
| Need to look up a skill or how-to in the vault | `memory -vault skills "..."` |
| Need architecture or engine docs | `memory -vault internals "..."` |
| Check for prior work before starting a task | `memory -query` + `memory -blobs` |
| User mentions a preference | `memory -pref key value` |
| After a significant task completes | `memory -write` with a summary |

---

## Tips

- `-query` is the broadest sweep — start there, then narrow with `-vault <bucket>` if needed
- Vault search requires the bucket to have been reindexed (`vault -reindex <bucket>`)
  at least once — a fresh install needs that first
- Preferences set with `-pref` are injected into every prompt automatically
- Task blobs contain full detail records — load them only when you need specifics
