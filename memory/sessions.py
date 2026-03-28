"""
Session lifecycle — start, end, load, list.

A session is one continuous chat instance.  Opening one creates a UUID row
in `sessions`; closing one writes a Tier 2 summary that also becomes a
`compression` conversation entry so it carries forward automatically.

Usage:
    from memory.sessions import start_session, end_session, load_session, list_sessions

    sid = start_session(conn)
    ...
    end_session(conn, sid, agent_call)
    rows = load_session(conn, sid)
    all_sessions = list_sessions(conn)
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_session_agent_prompt() -> str:
    """Load sessionAgent.md from disk (fresh on every call)."""
    path = BASE_DIR / "data" / "agent_files" / "sessionAgent.md"
    if not path.exists():
        return (
            "You are a session-close summarizer. "
            "Read the task and trivial summaries provided and write a single "
            "paragraph summarizing the session. Respond ONLY in JSON: "
            '{"summary": "your paragraph here"}'
        )
    return path.read_text(encoding="utf-8")


# ── Public API ─────────────────────────────────────────────────────────────────

def start_session(conn: sqlite3.Connection) -> str:
    """
    Open a new session.

    Inserts a row into `sessions` with a fresh UUID and the current
    timestamp.  Returns the session_id for use in all subsequent DB writes.
    """
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
        (session_id, _now()),
    )
    conn.commit()
    return session_id


def end_session(
    conn: sqlite3.Connection,
    session_id: str,
    agent_call=None,
) -> str:
    """
    Close a session and write a Tier 2 summary.

    Steps:
      1. Set `ended_at` on the session row.
      2. Gather all `task_summary` and `trivial_summary` conversation entries
         for this session.
      3. Call the Tier 2 agent with `sessionAgent.md` to produce a summary.
      4. Write the summary to `sessions.summary`.
      5. Write the same summary as a `compression` conversation entry so it
         carries forward into the next session's context.

    Args:
        conn:        Live DB connection.
        session_id:  The session to close.
        agent_call:  Callable with signature (messages, system, tier) -> dict.
                     If None, a simple concatenation fallback is used.

    Returns:
        The summary string that was written.
    """
    # 1. Mark session ended
    conn.execute(
        "UPDATE sessions SET ended_at = ? WHERE id = ?",
        (_now(), session_id),
    )
    conn.commit()

    # 2. Gather summaries for this session
    rows = conn.execute(
        """SELECT entry_type, content, date FROM conversation
           WHERE session_id = ?
             AND entry_type IN ('task_summary', 'trivial_summary')
           ORDER BY created_at""",
        (session_id,),
    ).fetchall()

    if not rows:
        summary = "Empty session — no tasks completed."
    elif agent_call is not None:
        # 3. Call Tier 2 agent with sessionAgent.md
        entries_text = "\n".join(
            f"[{r['entry_type']}] {r['content']}" for r in rows
        )
        system = _load_session_agent_prompt()
        messages = [
            {
                "role": "user",
                "content": (
                    f"Summarize this session ({len(rows)} entries):\n\n"
                    f"{entries_text}"
                ),
            }
        ]
        try:
            result = agent_call(messages, system, 2)
            summary = result.get("summary", str(result))
        except Exception as exc:
            # Fallback if agent call fails — never lose the session close
            summary = _fallback_summary(rows)
    else:
        summary = _fallback_summary(rows)

    # 4. Write summary to sessions table
    conn.execute(
        "UPDATE sessions SET summary = ? WHERE id = ?",
        (summary, session_id),
    )

    # 5. Write as compression entry in conversation
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        """INSERT INTO conversation (session_id, entry_type, content, date)
           VALUES (?, 'compression', ?, ?)""",
        (session_id, summary, today),
    )
    conn.commit()

    return summary


def load_session(conn: sqlite3.Connection, session_id: str) -> list[dict]:
    """
    Reopen a past session.

    Clears `ended_at` on the session row (making it active again) and
    returns all conversation entries for the session in chronological order.
    """
    # Verify session exists
    row = conn.execute(
        "SELECT id FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Session {session_id!r} not found.")

    # Clear ended_at — session is now active again
    conn.execute(
        "UPDATE sessions SET ended_at = NULL WHERE id = ?",
        (session_id,),
    )
    conn.commit()

    # Return all conversation entries for this session
    rows = conn.execute(
        """SELECT id, session_id, entry_type, role, content, task_id, date,
                  summarized, created_at
           FROM conversation
           WHERE session_id = ?
           ORDER BY created_at""",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def list_sessions(conn: sqlite3.Connection) -> list[dict]:
    """
    Return all sessions with id, started_at, ended_at, and the first
    100 characters of summary (for preview display).
    """
    rows = conn.execute(
        """SELECT id, started_at, ended_at,
                  SUBSTR(summary, 1, 100) AS summary_preview
           FROM sessions
           ORDER BY started_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_session(conn: sqlite3.Connection, session_id: str) -> dict | None:
    """Return full session row, or None if not found."""
    row = conn.execute(
        "SELECT * FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


# ── Fallback ───────────────────────────────────────────────────────────────────

def _fallback_summary(rows) -> str:
    """
    Produce a basic summary without calling the agent.
    Used when no agent_call is provided or if the call fails.
    """
    parts = []
    for r in rows:
        parts.append(r["content"] if isinstance(r, dict) else r["content"])
    joined = " | ".join(parts)
    if len(joined) > 500:
        joined = joined[:497] + "..."
    return f"Session summary: {joined}"