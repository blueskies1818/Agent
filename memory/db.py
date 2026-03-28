"""
SQLite setup, table creation, and low-level row helpers.

All other memory modules import from here — this is the only file that knows
the DB path or touches sqlite3 directly. Everything above this layer works with
plain Python dicts.

Usage:
    from memory.db import init_db, insert, fetch_one, fetch_all, update

    conn = init_db()
    insert("long_term", {"key": "user_name", "value": "Alice"})
    row  = fetch_one("long_term", {"key": "user_name"})
    rows = fetch_all("long_term", {})
    update("long_term", {"value": "Bob"}, {"key": "user_name"})
"""

import sqlite3
from pathlib import Path
from typing import Any

# Import db_path from the master config so this stays the single source of truth.
from config import MEMORY

DB_PATH: Path = MEMORY["db_path"]

# ── Schema ─────────────────────────────────────────────────────────────────────

_SCHEMA = """
-- Permanent user preferences, behavioral overrides, project list.
-- Never expires, never summarized away.
CREATE TABLE IF NOT EXISTS long_term (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Session registry — one row per chat instance.
-- A session opens when the assistant starts and closes when the user exits.
-- Past sessions can be reopened; loading one restores conversation entries into context.
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,   -- UUID generated at session start
    started_at  TIMESTAMP,
    ended_at    TIMESTAMP,          -- NULL = session still active
    summary     TEXT                -- Tier 2 summary written at session close
);

-- Full detail blobs — the permanent raw record of every completed complex task.
-- Never auto-loaded; the AI sees metadata in blob_index and retrieves on demand.
CREATE TABLE IF NOT EXISTS task_blobs (
    id          TEXT PRIMARY KEY,       -- UUID
    task_id     TEXT NOT NULL,          -- FK → tasks.id
    session_id  TEXT NOT NULL,          -- FK → sessions.id
    name        TEXT NOT NULL,          -- Descriptive slug e.g. "build_config_system"
    summary     TEXT NOT NULL,          -- One/two sentence description for retrieval hints
    tags        TEXT,                   -- Comma-separated topic tags e.g. "memory,sqlite,db"
    content     TEXT NOT NULL,          -- Full markdown: node messages, decisions, outputs
    date        TEXT NOT NULL,          -- ISO date string "YYYY-MM-DD"
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Master index of all blobs — queryable by date, tag, name, or session.
-- Never pruned, never expires — the permanent searchable record of all past work.
CREATE TABLE IF NOT EXISTS blob_index (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    blob_id      TEXT NOT NULL,         -- FK → task_blobs.id
    blob_name    TEXT NOT NULL,         -- Duplicated for fast lookup without JOIN
    blob_summary TEXT NOT NULL,         -- Duplicated for injection without loading content
    tags         TEXT,                  -- Duplicated for fast tag search
    session_id   TEXT NOT NULL,         -- FK → sessions.id
    date         TEXT NOT NULL,         -- ISO date string — primary axis for date queries
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Inter-node communication within a task.
-- Work nodes write messages here; subsequent nodes read them.
CREATE TABLE IF NOT EXISTS node_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT NOT NULL,
    from_node   INTEGER,
    to_node     INTEGER,                -- NULL = broadcast to all subsequent nodes
    key         TEXT,                   -- 'main' | 'files_written' | 'functions' | 'decisions' | 'handoff' | 'errors'
    value       TEXT,
    written_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    consumed    INTEGER DEFAULT 0
);

-- Task lifecycle record — one row per user request.
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,        -- FK → sessions.id
    user_prompt   TEXT,
    status        TEXT,                 -- 'planning' | 'working' | 'summarizing' | 'done' | 'failed'
    workflow_type TEXT,                 -- 'trivial' | 'complex'
    tier          INTEGER,
    plan_json     TEXT,
    date          TEXT,                 -- ISO date string
    created_at    TIMESTAMP,
    completed_at  TIMESTAMP
);

-- Rolling conversational memory.
-- Five entry types coexist here: turn | trivial_summary | plan_record | task_summary | compression.
-- When a past session is loaded, its entries are restored into active context by session_id.
CREATE TABLE IF NOT EXISTS conversation (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,         -- FK → sessions.id
    entry_type   TEXT NOT NULL,         -- 'turn' | 'trivial_summary' | 'plan_record' | 'task_summary' | 'compression'
    role         TEXT,                  -- 'user' | 'assistant' — only for entry_type='turn'
    content      TEXT NOT NULL,
    task_id      TEXT,                  -- FK → tasks.id
    date         TEXT,                  -- ISO date string — set on all non-turn entries
    summarized   INTEGER DEFAULT 0,     -- 1 = absorbed into a compression entry, excluded from injection
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Internal skill execution audit — never surfaced to agents.
-- One row per skill call regardless of success or failure.
CREATE TABLE IF NOT EXISTS skill_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT,
    node_id      INTEGER,
    skill_name   TEXT,
    input_json   TEXT,
    result_json  TEXT,
    success      INTEGER,
    error        TEXT,
    executed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# ── Connection ─────────────────────────────────────────────────────────────────

def init_db(db_path: Path | None = None) -> sqlite3.Connection:
    """
    Open (or create) the SQLite database, run all CREATE TABLE IF NOT EXISTS
    statements, and return the live connection.

    Passing db_path overrides the config value — useful in tests.
    Row factory is set to sqlite3.Row so callers can access columns by name.
    """
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")       # Safe for concurrent reads
    conn.execute("PRAGMA busy_timeout=5000;")      # Wait up to 5s on locked DB
    conn.execute("PRAGMA foreign_keys=ON;")        # Enforce FK constraints
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def get_table_names(conn: sqlite3.Connection) -> list[str]:
    """Return the names of all user-defined tables in the database."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
    ).fetchall()
    return [row["name"] for row in rows]


# ── Row Helpers ────────────────────────────────────────────────────────────────
# All helpers accept plain dicts and return plain dicts (or lists of dicts).
# Callers never touch sqlite3 internals directly.

def _row_to_dict(row: sqlite3.Row | None) -> dict | None:
    """Convert a sqlite3.Row to a plain dict, or pass through None."""
    return dict(row) if row is not None else None


def _build_where(where_dict: dict[str, Any]) -> tuple[str, list]:
    """
    Build a parameterised WHERE clause from a dict.

    Example:
        where_dict = {"session_id": "abc", "status": "done"}
        → ("WHERE session_id = ? AND status = ?", ["abc", "done"])

    Returns an empty clause and list if where_dict is empty.
    """
    if not where_dict:
        return "", []
    clauses = [f"{col} = ?" for col in where_dict]
    return "WHERE " + " AND ".join(clauses), list(where_dict.values())


def insert(conn: sqlite3.Connection, table: str, data: dict[str, Any]) -> int:
    """
    Insert a single row into `table`.

    Args:
        conn:  Live DB connection from init_db().
        table: Table name (e.g. "long_term").
        data:  Column → value mapping for the new row.

    Returns:
        The rowid of the inserted row (useful for AUTOINCREMENT tables).
    """
    cols = ", ".join(data.keys())
    placeholders = ", ".join("?" * len(data))
    sql = f"INSERT INTO {table} ({cols}) VALUES ({placeholders})"
    cur = conn.execute(sql, list(data.values()))
    conn.commit()
    return cur.lastrowid


def fetch_one(
    conn: sqlite3.Connection,
    table: str,
    where: dict[str, Any],
) -> dict | None:
    """
    Return the first row matching `where`, or None if no match.

    Args:
        conn:  Live DB connection.
        table: Table name.
        where: Column → value filter. Empty dict returns the first row in the table.
    """
    clause, params = _build_where(where)
    row = conn.execute(f"SELECT * FROM {table} {clause} LIMIT 1", params).fetchone()
    return _row_to_dict(row)


def fetch_all(
    conn: sqlite3.Connection,
    table: str,
    where: dict[str, Any],
    order_by: str | None = None,
) -> list[dict]:
    """
    Return all rows matching `where` as a list of dicts.

    Args:
        conn:     Live DB connection.
        table:    Table name.
        where:    Column → value filter. Empty dict returns all rows.
        order_by: Optional ORDER BY clause e.g. "created_at DESC".
    """
    clause, params = _build_where(where)
    order = f"ORDER BY {order_by}" if order_by else ""
    rows = conn.execute(f"SELECT * FROM {table} {clause} {order}", params).fetchall()
    return [dict(row) for row in rows]


def update(
    conn: sqlite3.Connection,
    table: str,
    data: dict[str, Any],
    where: dict[str, Any],
) -> int:
    """
    Update rows in `table` matching `where` with the values in `data`.

    Args:
        conn:  Live DB connection.
        table: Table name.
        data:  Column → new value mapping.
        where: Column → value filter identifying the rows to update.

    Returns:
        Number of rows affected.
    """
    if not data:
        raise ValueError("update() called with empty data dict — nothing to set.")
    if not where:
        raise ValueError("update() called with empty where dict — refusing full-table update.")

    set_clause = ", ".join(f"{col} = ?" for col in data)
    where_clause, where_params = _build_where(where)
    sql = f"UPDATE {table} SET {set_clause} {where_clause}"
    cur = conn.execute(sql, list(data.values()) + where_params)
    conn.commit()
    return cur.rowcount


def delete(
    conn: sqlite3.Connection,
    table: str,
    where: dict[str, Any],
) -> int:
    """
    Delete rows in `table` matching `where`.

    Requires a non-empty where dict to prevent accidental full-table deletes.

    Returns:
        Number of rows deleted.
    """
    if not where:
        raise ValueError("delete() called with empty where dict — refusing full-table delete.")
    clause, params = _build_where(where)
    cur = conn.execute(f"DELETE FROM {table} {clause}", params)
    conn.commit()
    return cur.rowcount