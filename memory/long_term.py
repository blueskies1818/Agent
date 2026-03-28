"""
Long-term key-value memory — user preferences, behavioral overrides, project list.

Lives in the `long_term` table. Never expires, never gets summarized away.
Injected as a compact block at every agent phase.

Usage:
    from memory.long_term import get, set, get_all, delete

    set(conn, "user_name", "Alice")
    name = get(conn, "user_name")            # "Alice"
    prefs = get_all(conn)                    # [{"key": "user_name", "value": "Alice", ...}]
    delete(conn, "user_name")
"""

import sqlite3
from datetime import datetime, timezone


def get(conn: sqlite3.Connection, key: str) -> str | None:
    """
    Return the value for a long-term key, or None if it doesn't exist.
    """
    row = conn.execute(
        "SELECT value FROM long_term WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set(conn: sqlite3.Connection, key: str, value: str) -> None:
    """
    Insert or update a long-term key-value pair.

    Uses INSERT OR REPLACE so callers never need to check existence first.
    The updated_at timestamp is always refreshed.
    """
    conn.execute(
        """INSERT INTO long_term (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET
               value = excluded.value,
               updated_at = excluded.updated_at""",
        (key, value, _now()),
    )
    conn.commit()


def get_all(conn: sqlite3.Connection) -> list[dict]:
    """
    Return all long-term entries as a list of dicts.

    Each dict has keys: key, value, updated_at.
    Ordered alphabetically by key for deterministic injection.
    """
    rows = conn.execute(
        "SELECT key, value, updated_at FROM long_term ORDER BY key"
    ).fetchall()
    return [dict(row) for row in rows]


def delete(conn: sqlite3.Connection, key: str) -> bool:
    """
    Delete a long-term key. Returns True if a row was actually removed.
    """
    cur = conn.execute("DELETE FROM long_term WHERE key = ?", (key,))
    conn.commit()
    return cur.rowcount > 0


def format_for_injection(conn: sqlite3.Connection) -> str:
    """
    Build the compact key-value block injected into agent context.

    Returns a string like:
        user_name: Alice
        timezone: America/New_York
        style: concise, no emoji

    Returns an empty string if no long-term entries exist.
    """
    entries = get_all(conn)
    if not entries:
        return ""
    lines = [f"{e['key']}: {e['value']}" for e in entries]
    return "\n".join(lines)


def _now() -> str:
    """UTC timestamp string for updated_at."""
    return datetime.now(timezone.utc).isoformat()