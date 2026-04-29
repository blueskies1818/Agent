"""
engine/mod_api.py — Shared API surface for all mods.

Two concerns live here:

  1. ModResult    — return type that carries text + optional media attachments.
                    Any mod can return attachments, not just debug_ui.
  2. Memory API   — log_action, save_fact, save_pref, recall.
                    Lets mods write to memory without importing internals.

Usage from any mod:

    from engine.mod_api import ModResult, MediaAttachment, log_action, save_fact

    def handle(args, raw):
        # Return text only (backward compatible — plain str also works)
        return "some output"

        # Return text + image (any mod can do this)
        return ModResult(
            text="screenshot captured",
            attachments=[MediaAttachment(type="image", path="/tmp/shot.png")],
        )
"""

from __future__ import annotations

import sqlite3
import sys  # for stderr warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone

from engine.media import MediaAttachment


# ── ModResult ─────────────────────────────────────────────────────────────────

@dataclass
class ModResult:
    """
    Rich return type for mod handlers.

    Any mod handler can return either:
      - A plain str (backward compatible, text only)
      - A ModResult (text + optional media attachments)

    The MCPRouter normalizes both to ModResult before passing upstream.
    The actor node passes attachments through the media pipeline to build
    provider-specific multimodal LLM messages.

    Attributes:
        text:        The text output shown to the AI (and in the terminal).
        attachments: Optional list of MediaAttachment objects.  Each attachment
                     is processed by engine/media.py before reaching the LLM.
                     Attachments are NOT stored in message history across turns
                     ("see once, discard").
                     Use log_action() to persist a text description instead.
    """
    text: str
    attachments: list[MediaAttachment] = field(default_factory=list)


# ── Action logging ────────────────────────────────────────────────────────────

def log_action(description: str, source: str = "mod") -> None:
    """
    Log a short action description to conversation memory.

    This is the primary tool for keeping context lean.  Instead of
    persisting expensive data (screenshots, large outputs), mods call
    this to record what happened in plain text.

    Written to the conversation table as a 'mod_action' entry.

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
    except Exception as e:
        print(f"[warn] log_action db write failed: {e}", file=sys.stderr)


def log_actions(descriptions: list[str], source: str = "mod") -> None:
    """Log multiple action descriptions at once."""
    for desc in descriptions:
        log_action(desc, source=source)


# ── Fact persistence ──────────────────────────────────────────────────────────

def save_fact(fact: str) -> None:
    """
    Save a durable fact that persists across sessions.
    Written to SQLite (long_term table) and ChromaDB.
    """
    if not fact or not fact.strip():
        return
    try:
        from memory.memory import write_memory
        write_memory(fact.strip())
    except Exception as e:
        print(f"[warn] save_fact failed: {e}", file=sys.stderr)


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
    from memory.db import get_db
    return get_db()


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