# Log

`core/log.py` — Unified logging singleton.

## Overview

Import and call anywhere with no setup:

```python
from core.log import log

log.info("sandbox ready", source="sandbox")
log.error("mod failed to load", source="mod_router")
log.fatal("provider not found — cannot continue")  # logs then raises SystemExit
```

## Output Format

```
[14:32:01] [INFO]  [sandbox] Container started
[14:32:05] [ERROR] [mod_router] Failed to load mods/debug_ui
[14:32:09] [FATAL] Provider not found — cannot continue
```

## Methods

| Method  | Level | Output  | Behavior |
|---------|-------|---------|----------|
| `info`  | INFO  | stdout  | Dim white; normal operational messages |
| `error` | ERROR | stderr  | Yellow; non-fatal, logs and continues |
| `fatal` | FATAL | stderr  | Bold red; logs then raises `SystemExit(1)` |

The optional `source` kwarg adds a `[module]` tag to each line for easy filtering.

No third-party dependencies — pure stdlib.

## Connections

- [[internals/core]]
- [[internals/architecture]]

---

[[overview]]
