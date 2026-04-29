"""
memory/sessions.py — Session lifecycle management.

All session data is stored as JSON. A text-only abstraction is used for RAG
so that system-injected content (soul files, core_refs, mod indices) never
enters the sessions ChromaDB bucket — only conversation turns do.

Storage layout
──────────────
  workspace/sessions/conversations/<cid>.json      Glass AI conversation
  workspace/sessions/<session_id>.json             Glass Harness agent session

ChromaDB doc IDs (in the "sessions" bucket, text-only, no .md files):
  conv<safe_cid>           Glass AI conversation
  sess<safe_session_id>    Glass Harness agent session

Re-indexing is always an upsert — the same cid always maps to the same doc ID.
Deleting removes both the JSON file and the ChromaDB entry.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

_BASE_DIR  = Path(__file__).parent.parent
_SESS_DIR  = _BASE_DIR / "workspace" / "sessions"
_CONV_DIR  = _SESS_DIR / "conversations"


def _safe_id(raw: str) -> str:
    """Strip chars invalid for ChromaDB doc IDs and cap at 60 chars."""
    return re.sub(r"[^a-zA-Z0-9_-]", "", raw)[:60]


# ── Text abstraction (conversation turns only — no system content) ─────────────

def _conv_to_text(data: dict) -> str:
    """Extract only user/assistant turns as plain text for RAG indexing."""
    lines = []
    for msg in data.get("messages", []):
        role    = (msg.get("role") or "").capitalize()
        content = (msg.get("content") or "").strip()
        if role and content and role.lower() in ("user", "assistant"):
            lines.append(f"{role}: {content}")
    return "\n".join(lines)




# ── RAG helpers ────────────────────────────────────────────────────────────────

def _index(doc_id: str, text: str) -> None:
    """Upsert text into the sessions ChromaDB bucket (no file written)."""
    text = text.strip()
    if not text:
        return
    try:
        from memory.vault import create_bucket, index_text
        create_bucket("sessions")
        index_text("sessions", doc_id, text)
    except Exception as e:
        from core.log import log
        log.error(f"sessions._index failed for {doc_id}: {e}", source="sessions")


def _deindex(doc_id: str) -> None:
    """Remove a ChromaDB entry from the sessions bucket."""
    try:
        from memory.vault import delete_index
        delete_index("sessions", doc_id)
    except Exception as e:
        from core.log import log
        log.error(f"sessions._deindex failed for {doc_id}: {e}", source="sessions")


# ── Glass AI conversations ─────────────────────────────────────────────────────

def write_conversation(cid: str, data: dict) -> None:
    """
    Persist a Glass AI conversation as JSON and upsert it in ChromaDB.

    Always overwrites the existing file — calling this on every new message
    extends the conversation record without creating duplicates.
    """
    _CONV_DIR.mkdir(parents=True, exist_ok=True)
    (_CONV_DIR / f"{cid}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    _index(f"conv{_safe_id(cid)}", _conv_to_text(data))


def load_conversation(cid: str) -> dict | None:
    """Read a Glass AI conversation JSON. Returns None if not found."""
    path = _CONV_DIR / f"{cid}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def delete_conversation(cid: str) -> None:
    """
    Delete a Glass AI conversation and everything linked to it:
      - conversation JSON file + ChromaDB entry
      - queue_tasks rows in SQLite whose session == cid
      - plan files (.md) whose session == cid, plus their index entries
    """
    # Remove conversation file + RAG entry
    path = _CONV_DIR / f"{cid}.json"
    if path.exists():
        path.unlink()
    _deindex(f"conv{_safe_id(cid)}")

    # Remove queue_tasks rows from SQLite
    try:
        from memory.db import init_db
        conn = init_db()
        conn.execute("DELETE FROM queue_tasks WHERE session = ?", (cid,))
        conn.commit()
        conn.close()
    except Exception as e:
        from core.log import log
        log.error(f"delete_conversation SQLite cleanup failed: {e}", source="sessions")

    # Remove plan files that belong to this conversation
    _delete_plans_for_session(cid)


def _delete_plans_for_session(cid: str) -> None:
    """Delete plan .md files and index entries whose session field matches cid."""
    _PLANS_INDEX = _BASE_DIR / "workspace" / ".agent" / "plans" / "index.json"
    if not _PLANS_INDEX.exists():
        return
    try:
        index = json.loads(_PLANS_INDEX.read_text(encoding="utf-8"))
    except Exception:
        return

    to_delete = [
        entry for entry in index.values()
        if entry.get("session") == cid
    ]
    if not to_delete:
        return

    for entry in to_delete:
        # Delete the plan .md file
        plan_path = Path(entry.get("plan_path", ""))
        if plan_path.exists():
            try:
                plan_path.unlink()
            except Exception:
                pass
        # Remove from index
        index.pop(entry["task_id"], None)

    # Rewrite the trimmed index
    try:
        _PLANS_INDEX.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


def list_conversations() -> list[dict]:
    """List all Glass AI conversations, newest first (lightweight summaries)."""
    if not _CONV_DIR.exists():
        return []
    results = []
    for p in sorted(_CONV_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            results.append({
                "cid":           p.stem,
                "title":         data.get("title", p.stem)[:120],
                "ts":            data.get("ts"),
                "message_count": len(data.get("messages", [])),
                "updated_at":    datetime.fromtimestamp(
                    p.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            })
        except Exception:
            pass
    return results


# ── Glass Harness internal session API ────────────────────────────────────────

def open_session(session_id: str) -> None:
    """Register a new session in the sessions SQLite table."""
    try:
        from memory.db import init_db
        conn = init_db()
        conn.execute(
            "INSERT OR IGNORE INTO sessions (id, started_at) VALUES (?, ?)",
            (session_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        from core.log import log
        log.error(f"open_session failed: {e}", source="sessions")


def log_turn(session_id: str, role: str, content: str) -> None:
    """Append a conversation turn to the conversation SQLite table."""
    if not content or not content.strip():
        return
    try:
        from memory.db import init_db
        conn = init_db()
        conn.execute(
            """INSERT INTO conversation
               (session_id, entry_type, role, content, created_at)
               VALUES (?, 'turn', ?, ?, ?)""",
            (session_id, role.lower(), content.strip(),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        from core.log import log
        log.error(f"log_turn failed: {e}", source="sessions")


def close_session(session_id: str, summary: str = "") -> None:
    """Mark the session as ended in SQLite. Turn data is already in the DB."""
    ended_at = datetime.now(timezone.utc).isoformat()
    try:
        from memory.db import init_db
        conn = init_db()
        conn.execute(
            "UPDATE sessions SET ended_at = ?, summary = ? WHERE id = ?",
            (ended_at, summary.strip()[:500], session_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        from core.log import log
        log.error(f"close_session failed: {e}", source="sessions")


def list_sessions(limit: int = 20) -> list[dict]:
    """Return recent sessions as a list of dicts, newest first."""
    try:
        from memory.db import init_db
        conn = init_db()
        rows = conn.execute(
            """SELECT id, started_at, ended_at, summary FROM sessions
               WHERE ended_at IS NOT NULL
               ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def load_session_turns(session_id: str) -> list[dict]:
    """Return all conversation turns for a session, oldest first."""
    try:
        from memory.db import init_db
        conn = init_db()
        rows = conn.execute(
            """SELECT role, content, created_at FROM conversation
               WHERE session_id = ? AND entry_type = 'turn'
               ORDER BY created_at ASC""",
            (session_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []
