# HTTP Server & Scheduling

V2 introduces an HTTP server that accepts tasks via a queue and streams output back in real time, plus a scheduler that fires tasks on a cron/interval schedule. Both are spawned as subprocesses by `main.py` at startup.

---

## Startup sequence

```python
# main.py
_start_server()          # uvicorn engine.server:app → background subprocess
_wait_for_health()       # poll GET /health until 200 or 30s timeout
_start_scheduler()       # python engine/scheduler.py → background subprocess
_start_reactives()       # scan reactive/ for .py files defining NAME + run()
# → interactive REPL
```

Every subprocess registers an `atexit` handler so it is terminated when `main.py` exits.

---

## `engine/server.py` — FastAPI task server

The server is the single entry point for all agent work. The interactive REPL, the scheduler, and external HTTP clients all go through it.

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Returns `{"status": "ok"}` — used by `main.py` to wait for readiness |
| `POST` | `/queue` | Submit a task; returns `task_id` immediately |
| `GET` | `/stream/<task_id>` | SSE stream of typed output chunks |
| `GET` | `/tasks/<task_id>` | Task status and result |
| `GET` | `/tasks` | List all tasks; filter with `?status=queued\|running\|complete\|failed\|cancelled` |
| `DELETE` | `/tasks/<task_id>` | Cancel a queued or running task |
| `POST` | `/schedule` | Write a scheduled task JSON to `scheduled/` |
| `GET` | `/schedule` | List all scheduled task files |
| `DELETE` | `/schedule/<task_id>` | Delete a scheduled task file |

### Submitting a task

```bash
curl -s -X POST http://127.0.0.1:8765/queue \
  -H "Content-Type: application/json" \
  -d '{"prompt": "list the files in /workspace"}'
```

Response:
```json
{"task_id": "a3f8...", "status": "queued", "stream_url": "/stream/a3f8..."}
```

**Request body** (`TaskRequest`):

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | `str` | required | The user message to run |
| `session` | `str` | `"new"` | Session identifier (future: session continuity) |
| `skills` | `list[str]` | `[]` | Pre-load these skills before running |
| `priority` | `int` | `1` | Task priority (higher = sooner; not yet enforced) |
| `source` | `str\|None` | `None` | Tag identifying where the task came from |

### Streaming output

```bash
curl -s http://127.0.0.1:8765/stream/a3f8...
```

The stream is Server-Sent Events (SSE). Each event is a JSON object:

```
data: {"type": "work",   "content": "[work] Reading the config file"}
data: {"type": "shell",  "content": "$ cat /workspace/config.yml"}
data: {"type": "output", "content": "key: value\n..."}
data: {"type": "done",   "content": "Task complete."}
```

**Event types:**

| Type | Meaning |
|------|---------|
| `work` | Agent status line — what it is doing right now |
| `shell` | A shell command was dispatched |
| `output` | General output / final response text |
| `done` | Task completed successfully |
| `error` | Task failed or was cancelled |

If a client connects to `/stream/<task_id>` after the task has already completed, the server replays the result and sends `done` immediately. A keepalive comment (`: keepalive`) is sent every 30 seconds to prevent proxy timeouts.

### Task lifecycle

```
POST /queue
  → status: "queued"
  → pushed to asyncio.Queue

_worker() drains the queue one task at a time:
  → status: "running"
  → asyncio.to_thread(_run_agent_sync, ...)
     → AgentLoop().run(prompt)
     → stdout redirected → SSE broadcast per line
  → status: "complete" | "failed"
  → "done" | "error" SSE event sent

DELETE /tasks/<task_id>
  → status: "cancelled"  (if queued or running)
  → "error: Task cancelled." SSE event sent
```

Tasks are persisted to SQLite (`queue_tasks` table) throughout their lifecycle. Running task state is also held in memory (`_live_tasks`) for low-latency status reads.

### Output capture

The server redirects `sys.stdout` to a `_LineCapture` object while the agent runs. Each complete line is classified by content:

- Lines containing `[work]` or starting with `[agent]` → `work` event
- Lines starting with `$ ` or containing `[shell]` → `shell` event
- Everything else → `output` event

This lets `main.py`'s REPL and external SSE clients both receive the same typed stream.

---

## `engine/scheduler.py` — Scheduled task dispatcher

A standalone subprocess that polls `SCHEDULED_DIR` every 60 seconds and posts due tasks to the HTTP queue. It watches its parent PID and exits automatically when `main.py` terminates.

### Schedule file format

Scheduled tasks are JSON files in `scheduled/`. Create them via `POST /schedule` or write the JSON directly.

```json
{
  "task_id":   "morning-summary",
  "prompt":    "Summarise the workspace activity from the past 24 hours",
  "schedule": {
    "type":    "cron",
    "value":   "0 9 * * 1-5"
  },
  "termination": {
    "type":    "never"
  },
  "next_run":  "2026-04-14T09:00:00Z",
  "created_at": "2026-04-11T12:00:00Z"
}
```

**`schedule` object:**

| `type` | `value` format | Example |
|--------|---------------|---------|
| `once` | ISO 8601 datetime (in `next_run`) | Fire once at a specific time |
| `interval` | `Nd`, `Nh`, `Nm`, `Ns` | `"12h"` — every 12 hours |
| `cron` | 5-field cron expression | `"0 9 * * 1-5"` — weekdays at 9 AM |

**`termination` object:**

| `type` | Behaviour |
|--------|-----------|
| `never` | Runs indefinitely (default) |
| `after_completion` | Deleted once the dispatched queue task reaches `complete` or `failed` |
| `on_date` | Deleted once `termination.date` (ISO 8601) has passed |

**Managed fields** (updated by the scheduler, do not set manually):

| Field | Description |
|-------|-------------|
| `next_run` | ISO 8601 UTC datetime of the next scheduled fire |
| `last_run` | ISO 8601 UTC datetime of the last dispatch |
| `pending_task_id` | Queue task ID of an in-flight dispatch; scheduler waits for it to complete before firing again |

### Scan loop

Every 60 seconds (`SCAN_INTERVAL`):

1. For each `.json` file in `SCHEDULED_DIR`:
   - Check `on_date` termination — delete and skip if expired
   - Check `pending_task_id` — skip if a previous dispatch is still running; clear and retry if it completed
   - Check `next_run` — skip if not yet due
   - `POST /queue` with the task prompt → get a `queue_task_id`
   - Update `last_run`, compute `next_run` (for recurring tasks), write back to disk

### Cron support

The scheduler implements a full 5-field cron parser:

```
MIN  HOUR  DOM  MON  DOW
 0    9     *    *   1-5     → weekdays at 09:00
*/15  *     *    *    *      → every 15 minutes
 0    0     1    *    *      → midnight on the 1st of every month
```

Supports: `*`, `*/N`, `a-b`, `a-b/N`, comma-separated lists. DOM and DOW use OR semantics when both are restricted (standard cron behaviour). Day-of-week: `0`=Sun, `1`=Mon … `7`=Sun.

### Managing schedules via HTTP

```bash
# Create a scheduled task
curl -s -X POST http://127.0.0.1:8765/schedule \
  -H "Content-Type: application/json" \
  -d '{
    "task_id": "daily-backup",
    "prompt": "Compress the workspace and move it to /backups",
    "schedule_type": "cron",
    "schedule_value": "0 2 * * *"
  }'

# List all scheduled tasks
curl -s http://127.0.0.1:8765/schedule

# Delete a scheduled task
curl -s -X DELETE http://127.0.0.1:8765/schedule/daily-backup
```

---

## `engine/plan_manager.py` — Plan file API

`PlanManager` is the single interface for all plan file operations. Both the planner node and the actor node use it — the session-level singleton is held at module level in `nodes.py`.

### Plan file locations

| Mode | Path |
|------|------|
| Workspace mode (`PROJECT_DIR` set) | `<workspace>/.agent/plan.md` |
| Local mode | `memory/plans/<task_id>.md` |

A global index at `memory/plans/index.json` tracks all plans and their statuses.

### `PlanManager` API

```python
from engine.plan_manager import PlanManager

pm = PlanManager(workspace="/home/user/my-app")   # or PlanManager() for local mode

# Write a new plan (overwrites any existing plan for this session)
task_id = pm.write_plan(
    title="Refactor auth middleware",
    steps=["Read existing auth code", "Rewrite token storage", "Write tests", "Confirm to user"],
)
# → Creates plan file, updates index, returns task_id

# Read the full plan file as a string
content = pm.read_plan()

# Mark step N (1-indexed) complete, advance ← CURRENT to the next undone step
pm.step_done(2)

# Insert a new step after step N — becomes the new ← CURRENT
pm.inject_step(after_n=2, content_text="Install missing dependency first")

# Append a note to the ## Notes section
pm.add_note("Found two token stores; using sessions table only")

# Update plan status
pm.set_status("complete")   # "active" | "paused" | "complete" | "failed"

# Worker context injection — compact progress summary
log = pm.generate_project_log()
# → "[DONE] Step 1 — Read existing auth code\n[CURRENT] Step 2 — Rewrite token storage"

# Current step text (used by actor to build worker system prompt)
step = pm.current_step_text()

# 1-indexed position of ← CURRENT, or 0 if none
idx = pm.current_step_index()

# Resume a previously created plan by task_id
content = pm.resume("2026-04-11_refactor-auth-middleware")

# List all plans from the index
plans = pm.list_plans()
# → [{"task_id": ..., "title": ..., "status": ..., "workspace": ..., ...}, ...]
```

### Plan file format

See [architecture.md](architecture.md) for the full plan file format and frontmatter field reference.

---

## Example: full task flow via HTTP

```bash
# 1. Submit a task
TASK=$(curl -s -X POST http://127.0.0.1:8765/queue \
  -H "Content-Type: application/json" \
  -d '{"prompt": "create a hello.py that prints Hello World"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

echo "task_id: $TASK"

# 2. Stream the output
curl -s http://127.0.0.1:8765/stream/$TASK

# 3. Check the final result
curl -s http://127.0.0.1:8765/tasks/$TASK | python3 -m json.tool
```

The stream closes automatically when the agent emits a `done` or `error` event. The task result is also available at `/tasks/<task_id>` after completion.


[[overview]]
