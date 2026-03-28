"""
Task detail blobs + blob_index — write, read, query.

One blob per completed complex task — the permanent raw record of exactly
what happened.  The AI never loads these automatically; it sees metadata
in `blob_index` and calls `read_blob` on demand.

Usage:
    from memory.task_blobs import write_blob, read_blob, query_index

    write_blob(conn, task_id=..., session_id=..., name="build_config",
               summary="Built the config system", tags="config,setup",
               content="# Full detail...", date="2025-03-15")

    content = read_blob(conn, "build_config")

    hits = query_index(conn, date="yesterday")
    hits = query_index(conn, tags="memory,sqlite")
    hits = query_index(conn, keyword="refactor", days_back=14)
"""

import sqlite3
import uuid
from datetime import date as dt_date, timedelta


def write_blob(
    conn: sqlite3.Connection,
    task_id: str,
    session_id: str,
    name: str,
    summary: str,
    tags: str,
    content: str,
    date: str,
) -> str:
    """
    Write a task blob and its index entry.

    Inserts one row into `task_blobs` (full content) and one row into
    `blob_index` (searchable metadata).  Both share the same blob_id.

    Args:
        conn:       Live DB connection.
        task_id:    FK → tasks.id for the completed task.
        session_id: FK → sessions.id for the session that ran the task.
        name:       Descriptive slug, e.g. "build_config_system".
        summary:    One or two sentence description for retrieval hints.
        tags:       Comma-separated topic tags, e.g. "config,setup".
        content:    Full markdown record — node messages, decisions, outputs.
        date:       ISO date string "YYYY-MM-DD".

    Returns:
        The generated blob_id (UUID string).
    """
    blob_id = str(uuid.uuid4())

    conn.execute(
        """INSERT INTO task_blobs
           (id, task_id, session_id, name, summary, tags, content, date)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (blob_id, task_id, session_id, name, summary, tags, content, date),
    )

    conn.execute(
        """INSERT INTO blob_index
           (blob_id, blob_name, blob_summary, tags, session_id, date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (blob_id, name, summary, tags, session_id, date),
    )

    conn.commit()
    return blob_id


def read_blob(conn: sqlite3.Connection, name: str) -> str | None:
    """
    Load full blob content by name.

    Returns the markdown content string, or None if no blob with that
    name exists.
    """
    row = conn.execute(
        "SELECT content FROM task_blobs WHERE name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return row["content"] if row else None


def get_blob(conn: sqlite3.Connection, name: str) -> dict | None:
    """
    Load the full blob row by name.

    Returns all fields as a dict, or None if not found.
    """
    row = conn.execute(
        "SELECT * FROM task_blobs WHERE name = ? LIMIT 1",
        (name,),
    ).fetchone()
    return dict(row) if row else None


def query_index(
    conn: sqlite3.Connection,
    date: str | None = None,
    tags: str | None = None,
    keyword: str | None = None,
    days_back: int | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """
    Search blob_index for matching entries.  Returns metadata only —
    never full content.

    Args:
        conn:       Live DB connection.
        date:       "today", "yesterday", or ISO date string "YYYY-MM-DD".
        tags:       Comma-separated tags (OR match across tags).
        keyword:    Substring search in blob_name and blob_summary.
        days_back:  Return blobs from the last N days.
                    Default 7 if no date/keyword/tags/session_id given.
        session_id: Filter by session.

    Returns:
        List of dicts, each with: name, summary, date, tags.
    """
    conditions: list[str] = []
    values: list = []

    # ── Date filtering ─────────────────────────────────────────────────────
    if date is not None:
        resolved = _resolve_date(date)
        conditions.append("date = ?")
        values.append(resolved)
    elif days_back is not None:
        cutoff = (dt_date.today() - timedelta(days=days_back)).isoformat()
        conditions.append("date >= ?")
        values.append(cutoff)
    elif not tags and not keyword and not session_id:
        # Default window if no other filter provided
        cutoff = (dt_date.today() - timedelta(days=7)).isoformat()
        conditions.append("date >= ?")
        values.append(cutoff)

    # ── Tag filtering (OR) ─────────────────────────────────────────────────
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            tag_clauses = ["tags LIKE ?" for _ in tag_list]
            conditions.append(f"({' OR '.join(tag_clauses)})")
            values.extend(f"%{t}%" for t in tag_list)

    # ── Keyword search ─────────────────────────────────────────────────────
    if keyword:
        conditions.append("(blob_name LIKE ? OR blob_summary LIKE ?)")
        values.extend([f"%{keyword}%", f"%{keyword}%"])

    # ── Session filter ─────────────────────────────────────────────────────
    if session_id:
        conditions.append("session_id = ?")
        values.append(session_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT blob_name AS name, blob_summary AS summary, date, tags "
        f"FROM blob_index {where} ORDER BY date DESC, created_at DESC"
    )

    rows = conn.execute(sql, values).fetchall()
    return [dict(r) for r in rows]


def format_for_injection(conn: sqlite3.Connection, days_back: int = 7) -> str:
    """
    Build the blob_index_recent block for context injection.

    Returns a compact list of recent blobs:
        build_config — Built the master config system [2025-03-15]
        refactor_memory — Rewrote memory layer to use SQLite [2025-03-16]

    Returns empty string if no recent blobs.
    """
    hits = query_index(conn, days_back=days_back)
    if not hits:
        return ""
    lines = []
    for h in hits:
        lines.append(f"{h['name']} — {h['summary']} [{h['date']}]")
    return "\n".join(lines)


# ── Private ────────────────────────────────────────────────────────────────────

def _resolve_date(date_str: str) -> str:
    """Convert 'today', 'yesterday', or pass through ISO strings."""
    lower = date_str.strip().lower()
    if lower == "today":
        return dt_date.today().isoformat()
    if lower == "yesterday":
        return (dt_date.today() - timedelta(days=1)).isoformat()
    return date_str.strip()