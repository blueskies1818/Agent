---
description: Query, read, and write persistent memory that survives across sessions.
keywords: remember, recall, memory, forget, do you know, what do you know, have you seen, previous session, last time, earlier, before, preference
---

# Memory — query, read, and write persistent memory

Use shell commands to interact with long-term memory that persists across sessions.
Memory is searched across all stores: conversation history, task blobs, preferences,
semantic embeddings (RAG), and the flat memory file.

## Commands

### Search memory (most useful — searches everything)
```
memory -query "what do i know about PyQt6"
```
This searches across ALL memory stores at once:
- Long-term preferences
- Completed task summaries and blob metadata
- Conversation history (summaries, compressions)
- Semantic embeddings (RAG / ChromaDB)
- Flat memory file

### Write a fact to memory
```
memory -write "user prefers dark mode in all UIs"
```

### Read the full flat memory file
```
memory -read
```

### List long-term preferences
```
memory -prefs
```

### Set a preference
```
memory -pref timezone America/New_York
memory -pref style concise, no emoji
```

### List recent task blobs (completed task records)
```
memory -blobs
memory -blobs tags=memory,sqlite
memory -blobs days=14
```

### Load full content of a specific task blob
```
memory -blob build_config_system
```

## When to use
- User asks "do you remember..." or "what do you know about..."
- You need context from a previous session about what was built
- Before starting a task, check if you've done something similar before
- User mentions a preference or behavioral override → write it with `-pref`
- After completing a significant task → write a summary with `-write`

## Tips
- Use `-query` first — it's the most comprehensive search
- Queries are keyword-matched, so use specific terms from the topic
- Only write facts that are genuinely useful across sessions
- Preferences (`-pref`) persist permanently and are injected into every prompt
- Task blobs contain full detail records — only load them if you need specifics