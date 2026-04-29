"""
engine/context_state.py — In-memory context snapshot register.

AgentLoop calls write_snapshot() after each run() turn.
The /debug/context endpoint and context_map.py read from here.
No file is written — server must be running to read context data.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.context_window import ContextWindow

_current: dict[str, Any] = {}


def _serialize_ctx(ctx: "ContextWindow", injected: dict | None = None) -> dict:
    used, total = ctx.token_usage

    # Count tokens in injected system-prompt sections (soul, core_ref, etc.)
    # These are built into the system string sent to the API but are NOT stored
    # as pages in the context window, so ctx.token_usage misses them entirely.
    injected_tokens = sum(
        len(v) // 4 for v in (injected or {}).values() if v
    )

    pages = []
    for page in ctx._pages:
        age = ctx._turn - page.turn_added
        recency = 1.0 / (1.0 + age * 0.2)
        score = ctx.score(page)
        pages.append({
            "source":          page.source,
            "relevance_score": round(page.relevance_score, 3),
            "recency":         round(recency, 3),
            "score":           round(score, 3),
            "turn_added":      page.turn_added,
            "age":             age,
            "tokens":          page.tokens,
            "content":         page.content,
        })
    pages.sort(key=lambda p: p["score"], reverse=True)
    result: dict[str, Any] = {
        "turn":              ctx.current_turn,
        "tokens_used":       used + injected_tokens,
        "tokens_injected":   injected_tokens,
        "tokens_pages":      used,
        "tokens_max":        total,
        "page_count":        len(pages),
        "pages":             pages,
    }
    if injected:
        result["injected"] = injected
    return result


def write_snapshot(
    planner_ctx: "ContextWindow",
    worker_ctx: "ContextWindow",
    task_id: str | None = None,
    planner_injected: dict | None = None,
    worker_injected: dict | None = None,
    messages_stats: dict | None = None,
) -> None:
    global _current
    _current = {
        "updated_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "task_id":        task_id,
        "planner":        _serialize_ctx(planner_ctx, planner_injected),
        "worker":         _serialize_ctx(worker_ctx, worker_injected),
        "messages_stats": messages_stats or {},
    }


def read_snapshot() -> dict:
    return _current
