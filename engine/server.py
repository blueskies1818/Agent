"""
engine/server.py — FastAPI HTTP server and in-process task queue.

Endpoints:
    GET    /health                   Startup readiness check
    POST   /queue                    Submit a task; returns task_id immediately
    GET    /stream/<task_id>         SSE stream of typed agent output chunks
    GET    /tasks/<task_id>          Task status and result
    GET    /tasks                    List all tasks (filter by ?status=)
    DELETE /tasks/<task_id>          Cancel a queued or running task
    POST   /schedule                 Write a scheduled task JSON to scheduled/
    GET    /schedule                 List all scheduled task files
    DELETE /schedule/<task_id>       Delete a scheduled task file

SSE event types:
    work    — agent status line (what it's doing right now)
    shell   — shell command being executed
    output  — general output / final response text
    done    — task completed successfully
    error   — task failed or was cancelled

Run directly:
    python engine/server.py
Or via uvicorn:
    uvicorn engine.server:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import asyncio
import io
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from config import SCHEDULED_DIR, SERVER_HOST, SERVER_PORT
from memory.db import init_db

app = FastAPI(title="Agent Server", version="2.0")


# ── In-process task queue ──────────────────────────────────────────────────────

_task_queue: asyncio.Queue[dict] = asyncio.Queue()

# task_id → list of asyncio.Queue (one per live SSE subscriber)
_subscribers: dict[str, list[asyncio.Queue]] = {}

# task_id → task dict (in-memory; authoritative for running tasks)
_live_tasks: dict[str, dict] = {}


# ── Utilities ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── DB helpers ────────────────────────────────────────────────────────────────

def _db_upsert(task: dict) -> None:
    conn = init_db()
    conn.execute(
        """
        INSERT OR REPLACE INTO queue_tasks
            (id, prompt, session, status, result, error,
             priority, skills_json, source, created_at, started_at, completed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            task["id"],
            task["prompt"],
            task.get("session", "new"),
            task["status"],
            task.get("result"),
            task.get("error"),
            task.get("priority", 1),
            json.dumps(task.get("skills", [])),
            task.get("source"),
            task.get("created_at"),
            task.get("started_at"),
            task.get("completed_at"),
        ),
    )
    conn.commit()
    conn.close()


def _db_load(task_id: str) -> dict | None:
    conn = init_db()
    row = conn.execute(
        "SELECT * FROM queue_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    d = dict(row)
    d["skills"] = json.loads(d.pop("skills_json") or "[]")
    return d


def _db_list(status: str | None = None) -> list[dict]:
    conn = init_db()
    if status:
        rows = conn.execute(
            "SELECT * FROM queue_tasks WHERE status = ? ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM queue_tasks ORDER BY created_at DESC"
        ).fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        d["skills"] = json.loads(d.pop("skills_json") or "[]")
        result.append(d)
    return result


# ── SSE broadcast ─────────────────────────────────────────────────────────────

async def _broadcast(task_id: str, event_type: str, content: str) -> None:
    msg = {"type": event_type, "content": content}
    for q in list(_subscribers.get(task_id, [])):
        await q.put(msg)


# ── Output capture ────────────────────────────────────────────────────────────

class _LineCapture(io.TextIOBase):
    """Wraps a callback; fires it for each complete newline-terminated line."""

    def __init__(self, callback):
        self._cb = callback
        self._buf = ""

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line:
                self._cb(line)
        return len(s)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if self._buf:
            self._cb(self._buf)
            self._buf = ""


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LOG_LINE_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] \[(?:INFO|ERROR|FATAL)\]")

# Prefixes (after ANSI strip) whose lines are shown only in the typing indicator
_WORK_PREFIXES = ("[work]", "[agent]", "[plan]", "[thinking", "[replanner]", "[blocked]", "[skill")
# Prefixes whose lines are filtered entirely (infra noise, never sent to browser)
_FILTER_PREFIXES = ("[context]", "[session log", "[graph]", "[debug]")


def _strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def _classify(line: str) -> tuple[str | None, str]:
    """
    Returns (event_type, clean_content).
    event_type is None → drop the line entirely (infra/log noise).
    """
    clean = _strip_ansi(line).strip()

    # Drop empty lines
    if not clean:
        return None, clean

    # Drop log lines: [HH:MM:SS] [INFO] ...
    if _LOG_LINE_RE.match(clean):
        return None, clean

    # Drop known infrastructure prefixes
    if any(clean.startswith(p) for p in _FILTER_PREFIXES):
        return None, clean

    # Detail lines (command output) → collapsible in thinking block
    if clean.startswith("[detail]"):
        return "detail", clean[8:].lstrip()

    # Web search progress lines — [search] becomes a thinking step; rest are collapsible detail
    if clean.startswith("[search]"):
        return "shell", clean
    if any(clean.startswith(p) for p in ("[fetch]", "[skip]", "[warn]", "[retry]")):
        return "detail", clean

    # Shell commands → shell event
    if clean.startswith("$ ") or "[shell]" in clean:
        return "shell", clean

    # Status/thinking lines → work event (typing indicator only)
    if any(clean.startswith(p) for p in _WORK_PREFIXES):
        return "work", clean

    # Everything else (actual response text) → output
    return "output", clean


def _run_agent_sync(task: dict, event_loop: asyncio.AbstractEventLoop) -> str:
    """
    Synchronous agent run — executes in a worker thread via asyncio.to_thread.

    Redirects sys.stdout so every print() call becomes an SSE event pushed
    back to the asyncio event loop via run_coroutine_threadsafe.
    """
    from engine.loop import AgentLoop

    task_id = task["id"]
    output_lines: list[str] = []

    def on_line(line: str) -> None:
        event_type, clean = _classify(line)
        if event_type is None:
            return  # filtered — don't send to browser
        if event_type == "output":
            output_lines.append(clean)
        asyncio.run_coroutine_threadsafe(
            _broadcast(task_id, event_type, clean),
            event_loop,
        )

    capture = _LineCapture(on_line)
    old_stdout = sys.stdout
    sys.stdout = capture
    try:
        # Expose the Glass AI session ID so plan files can be linked back to it
        from engine import nodes as _nodes
        _nodes._current_session = task.get("session") or None
        agent = AgentLoop()
        agent.run(task["prompt"])
        agent.close()
    finally:
        capture.close()
        sys.stdout = old_stdout
        from engine import nodes as _nodes
        _nodes._current_session = None

    return "\n".join(output_lines)


# ── Worker coroutine ──────────────────────────────────────────────────────────

async def _worker() -> None:
    """Single coroutine that drains _task_queue one task at a time."""
    while True:
        task = await _task_queue.get()
        task_id = task["id"]

        # Skip tasks cancelled before the worker picked them up
        if task.get("status") == "cancelled":
            _task_queue.task_done()
            continue

        task["status"] = "running"
        task["started_at"] = _now()
        _live_tasks[task_id] = task
        _db_upsert(task)

        event_loop = asyncio.get_running_loop()
        try:
            result = await asyncio.to_thread(_run_agent_sync, task, event_loop)
            task["status"] = "complete"
            task["result"] = result
            task["completed_at"] = _now()
            await _broadcast(task_id, "done", "Task complete.")
        except Exception as exc:
            task["status"] = "failed"
            task["error"] = str(exc)
            task["completed_at"] = _now()
            await _broadcast(task_id, "error", str(exc))
        finally:
            _db_upsert(task)
            _live_tasks[task_id] = task
            _task_queue.task_done()


# ── App lifecycle ─────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _on_startup() -> None:
    asyncio.create_task(_worker())


# ── Request / response models ─────────────────────────────────────────────────

class TaskRequest(BaseModel):
    prompt: str
    session: str = "new"
    skills: list[str] = []
    priority: int = 1
    source: str | None = None


class ScheduleRequest(BaseModel):
    task_id: str
    prompt: str
    schedule_type: str          # "once" | "interval" | "cron"
    schedule_value: str         # e.g. "60" (seconds) or "0 9 * * 1"
    skills: list[str] = []


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


# ── Debug ─────────────────────────────────────────────────────────────────────

@app.get("/debug/context")
async def debug_context() -> dict:
    from engine.context_state import read_snapshot
    snap = read_snapshot()
    if not snap:
        raise HTTPException(status_code=404, detail="No context snapshot available yet — run a task first")
    return snap


# ── Queue ─────────────────────────────────────────────────────────────────────

@app.post("/queue")
async def enqueue_task(req: TaskRequest) -> dict:
    task_id = str(uuid.uuid4())
    task = {
        "id":           task_id,
        "prompt":       req.prompt,
        "session":      req.session,
        "skills":       req.skills,
        "priority":     req.priority,
        "source":       req.source,
        "status":       "queued",
        "result":       None,
        "error":        None,
        "created_at":   _now(),
        "started_at":   None,
        "completed_at": None,
    }
    _live_tasks[task_id] = task
    _db_upsert(task)
    await _task_queue.put(task)
    return {"task_id": task_id, "status": "queued", "stream_url": f"/stream/{task_id}"}


# ── Task list / status / cancel ───────────────────────────────────────────────

@app.get("/tasks")
async def list_tasks(status: str | None = Query(default=None)) -> list[dict]:
    db_tasks = _db_list(status)
    # Merge with in-memory state — running tasks are more up-to-date there
    seen: set[str] = set()
    result: list[dict] = []
    for t in db_tasks:
        tid = t["id"]
        seen.add(tid)
        result.append(_live_tasks[tid] if tid in _live_tasks else t)
    return result


@app.get("/tasks/{task_id}")
async def get_task(task_id: str) -> dict:
    if task_id in _live_tasks:
        return _live_tasks[task_id]
    task = _db_load(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.delete("/tasks/{task_id}")
async def cancel_task(task_id: str) -> dict:
    task = _live_tasks.get(task_id) or _db_load(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] not in ("queued", "running"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot cancel task with status '{task['status']}'",
        )
    task["status"] = "cancelled"
    task["completed_at"] = _now()
    _live_tasks[task_id] = task
    _db_upsert(task)
    await _broadcast(task_id, "error", "Task cancelled.")
    return {"task_id": task_id, "status": "cancelled"}


# ── SSE stream ────────────────────────────────────────────────────────────────

async def _sse_gen(task_id: str) -> AsyncGenerator[str, None]:
    # If task is already terminal, replay result and close immediately
    task = _live_tasks.get(task_id) or _db_load(task_id)
    if task is not None:
        if task["status"] == "complete":
            if task.get("result"):
                yield f"data: {json.dumps({'type': 'output', 'content': task['result']})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'content': 'Task complete.'})}\n\n"
            return
        if task["status"] in ("failed", "cancelled"):
            yield f"data: {json.dumps({'type': 'error', 'content': task.get('error') or 'Task ended.'})}\n\n"
            return

    q: asyncio.Queue = asyncio.Queue()
    _subscribers.setdefault(task_id, []).append(q)
    try:
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Keepalive comment to prevent proxy timeouts
                yield ": keepalive\n\n"
                continue
            yield f"data: {json.dumps(event)}\n\n"
            if event["type"] in ("done", "error"):
                break
    finally:
        subs = _subscribers.get(task_id, [])
        if q in subs:
            subs.remove(q)
        if not subs:
            _subscribers.pop(task_id, None)


@app.get("/stream/{task_id}")
async def stream_task(task_id: str) -> StreamingResponse:
    return StreamingResponse(
        _sse_gen(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",
            "Connection":       "keep-alive",
        },
    )


# ── Scheduled tasks ───────────────────────────────────────────────────────────

@app.post("/schedule")
async def create_schedule(req: ScheduleRequest) -> dict:
    scheduled_dir = Path(SCHEDULED_DIR)
    scheduled_dir.mkdir(parents=True, exist_ok=True)
    path = scheduled_dir / f"{req.task_id}.json"
    data = req.model_dump()
    data["created_at"] = _now()
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"task_id": req.task_id, "status": "scheduled"}


@app.get("/schedule")
async def list_schedule() -> list[dict]:
    scheduled_dir = Path(SCHEDULED_DIR)
    if not scheduled_dir.exists():
        return []
    result = []
    for f in sorted(scheduled_dir.glob("*.json")):
        try:
            result.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return result


@app.delete("/schedule/{task_id}")
async def delete_schedule(task_id: str) -> dict:
    path = Path(SCHEDULED_DIR) / f"{task_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Scheduled task not found")
    path.unlink()
    return {"task_id": task_id, "deleted": True}


# ── Glass AI conversation session endpoints ───────────────────────────────────
# Called by Glass AI's Node server when the user switches or deletes a conversation.

@app.get("/conversations")
async def list_conversations() -> list[dict]:
    """List all Glass AI conversations (lightweight summaries, newest first)."""
    from memory.sessions import list_conversations as _list
    return await asyncio.to_thread(_list)


@app.post("/conversations/{cid}/reindex")
async def reindex_conversation(cid: str) -> dict:
    """
    Re-index a Glass AI conversation into the sessions RAG bucket.

    Glass AI calls this when the user switches away from a conversation so the
    latest messages are always reflected in ChromaDB.  Safe to call repeatedly —
    always updates the existing entry, never creates a duplicate.
    """
    from memory.sessions import load_conversation, write_conversation
    data = await asyncio.to_thread(load_conversation, cid)
    if data is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # write_conversation always overwrites the file and re-indexes in ChromaDB
    await asyncio.to_thread(write_conversation, cid, data)
    return {"cid": cid, "status": "reindexed"}


@app.delete("/conversations/{cid}")
async def delete_conversation(cid: str) -> dict:
    """
    Delete a Glass AI conversation from disk and from the ChromaDB index.

    Called when the user deletes a chat in Glass AI.
    """
    from memory.sessions import delete_conversation as _delete
    await asyncio.to_thread(_delete, cid)
    return {"cid": cid, "deleted": True}


@app.post("/conversations/{cid}")
async def write_conversation(cid: str, body: dict) -> dict:
    """
    Persist a Glass AI conversation JSON and re-index it.

    Glass AI POSTs the full conversation data here.  The session JSON is written
    to workspace/sessions/conversations/<cid>.json and upserted in ChromaDB.
    """
    from memory.sessions import write_conversation as _write
    await asyncio.to_thread(_write, cid, body)
    return {"cid": cid, "status": "saved"}


# ── Standalone entrypoint ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("engine.server:app", host=SERVER_HOST, port=SERVER_PORT, reload=False)
