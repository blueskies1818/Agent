"""
engine/mod_api.py — Shared API surface for all mods.

Two concerns live here:

  1. ModResult    — return type that carries text + optional images.
                    Any mod can return images, not just debug_ui.
  2. Memory API   — log_action, save_fact, save_pref, recall.
                    Lets mods write to memory without importing internals.

Usage from any mod:

    from engine.mod_api import ModResult, log_action, save_fact

    def handle(args, raw):
        # Return text only (backward compatible — plain str also works)
        return "some output"

        # Return text + image (any mod can do this)
        return ModResult(
            text="clicked at (340, 220)",
            images=[screenshot_bytes],
        )
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone


# ── ModResult ─────────────────────────────────────────────────────────────────

@dataclass
class ModResult:
    """
    Rich return type for mod handlers.

    Any mod handler can return either:
      - A plain str (backward compatible, text only)
      - A ModResult (text + optional image attachments)

    The ModRouter normalizes both to ModResult before passing upstream.
    The actor node checks for images and builds multimodal LLM messages
    when they're present.

    Attributes:
        text:   The text output shown to the AI (and in the terminal).
        images: Optional list of PNG image bytes.  Each image is included
                in the LLM message as a vision content block.  Images are
                NOT stored in memory — they're seen once and evicted.
                Use log_action() to persist a text description instead.
    """
    text: str
    images: list[bytes] = field(default_factory=list)


# ── Action logging ────────────────────────────────────────────────────────────

def log_action(description: str, source: str = "mod") -> None:
    """
    Log a short action description to conversation memory.

    This is the primary tool for keeping context lean.  Instead of
    persisting expensive data (screenshots, large outputs), mods call
    this to record what happened in plain text.

    Written to the conversation table as a 'mod_action' entry,
    with a fallback to the flat memory file.

    Args:
        description:  What happened, in a few words.
        source:       Tag for the mod that logged this.
    """
    if not description or not description.strip():
        return

    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    entry = f"[{timestamp}] [{source}] {description.strip()}"

    try:
        conn = _get_db()
        if conn:
            session_id = _get_active_session(conn)
            if session_id:
                conn.execute(
                    """INSERT INTO conversation
                       (session_id, entry_type, content, date)
                       VALUES (?, 'mod_action', ?, ?)""",
                    (session_id, entry, _today()),
                )
                conn.commit()
                return
    except Exception:
        pass

    try:
        _append_to_flat_memory(entry)
    except Exception:
        pass


def log_actions(descriptions: list[str], source: str = "mod") -> None:
    """Log multiple action descriptions at once."""
    for desc in descriptions:
        log_action(desc, source=source)


# ── Fact persistence ──────────────────────────────────────────────────────────

def save_fact(fact: str) -> None:
    """
    Save a durable fact that persists across sessions.
    Written to both the flat memory file and ChromaDB (if available).
    """
    if not fact or not fact.strip():
        return
    try:
        from memory.memory import write_memory
        write_memory(fact.strip())
    except Exception:
        try:
            _append_to_flat_memory(fact.strip())
        except Exception:
            pass


# ── Preferences ───────────────────────────────────────────────────────────────

def save_pref(key: str, value: str) -> None:
    """Save a long-term preference (permanent, never expires)."""
    try:
        conn = _get_db()
        if conn:
            from memory.long_term import set as lt_set
            lt_set(conn, key, value)
    except Exception:
        pass


def get_pref(key: str) -> str | None:
    """Read a long-term preference by key."""
    try:
        conn = _get_db()
        if conn:
            from memory.long_term import get as lt_get
            return lt_get(conn, key)
    except Exception:
        pass
    return None


# ── Recall ────────────────────────────────────────────────────────────────────

def recall(query: str, top_k: int = 5) -> list[str]:
    """
    Quick semantic search across all memory stores.
    Returns relevant text snippets, most relevant first.
    """
    results: list[str] = []

    try:
        from memory.rag import MemoryRetriever
        from config import RAG_MIN_SCORE
        retriever = MemoryRetriever(min_score=RAG_MIN_SCORE)
        for text, score in retriever.retrieve(query, top_k=top_k):
            results.append(text)
    except Exception:
        pass

    try:
        conn = _get_db()
        if conn:
            keyword = f"%{query}%"
            rows = conn.execute(
                """SELECT content FROM conversation
                   WHERE entry_type IN ('mod_action', 'task_summary',
                                        'plan_record', 'compression')
                     AND content LIKE ?
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (keyword, top_k),
            ).fetchall()
            for r in rows:
                text = r["content"]
                if text not in results:
                    results.append(text)
    except Exception:
        pass

    return results[:top_k]


# ── Internals ─────────────────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection | None:
    try:
        from memory.db import init_db
        return init_db()
    except Exception:
        return None


def _get_active_session(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            """SELECT id FROM sessions
               WHERE ended_at IS NULL
               ORDER BY started_at DESC
               LIMIT 1"""
        ).fetchone()
        return row["id"] if row else None
    except Exception:
        return None


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _append_to_flat_memory(text: str) -> None:
    from config import MEMORY_FILE
    from pathlib import Path
    path = Path(MEMORY_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n")