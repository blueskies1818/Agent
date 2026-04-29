"""
memory/memory.py — Persistent memory and session logging.

Memory model (V2):
  memory/agent.db     -> SQLite: authoritative structured store (sessions, turns,
                         task records, long-term facts).  Never lost even if
                         ChromaDB is unavailable.
  memory/chroma/      -> ChromaDB: semantic search index only.  Rebuildable from
                         agent.db if corrupted.  If unavailable, RAG returns empty
                         — agent still works, just without semantic memory hints.
  memory/logs/        -> Per-session JSON logs (written lazily — ghost sessions
                         that stay conversational leave no file behind).

No flat file (memory.txt) — ChromaDB + SQLite are the single source of truth.
"""

import json
import os
from datetime import datetime

from config import LOGS_DIR


# ── Persistent memory ─────────────────────────────────────────────────────────

def read_memory() -> str:
    """
    Return all stored memory facts as a single string.

    Reads from the SQLite long_term table (keys prefixed with 'memory:').
    Returns empty string if nothing is stored or the DB is unavailable.
    """
    try:
        from memory.db import init_db
        conn = init_db()
        rows = conn.execute(
            "SELECT value FROM long_term WHERE key LIKE 'memory:%' ORDER BY updated_at ASC"
        ).fetchall()
        conn.close()
        if not rows:
            return ""
        return "\n\n".join(row[0] for row in rows)
    except Exception:
        return ""


def write_memory(content: str) -> None:
    """
    Persist a fact to SQLite (authoritative) and ChromaDB (semantic index).

    SQLite write always happens first.  ChromaDB embedding is best-effort —
    if Ollama is unavailable the fact is still saved to SQLite and retrievable
    via read_memory().
    """
    content = content.strip()
    if not content:
        return

    import hashlib
    key = "memory:" + hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    timestamp = datetime.now().isoformat()

    # 1. Always write to SQLite long_term
    try:
        from memory.db import init_db
        conn = init_db()
        conn.execute(
            "INSERT OR IGNORE INTO long_term (key, value, updated_at) VALUES (?, ?, ?)",
            (key, content, timestamp),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        from core.log import log
        log.error(f"SQLite write failed: {e}", source="memory")

    # 2. Best-effort embed into ChromaDB for semantic retrieval
    try:
        from memory.embedder import embed_and_store
        embed_and_store(content, metadata={"source": "agent", "timestamp": timestamp})
    except Exception as e:
        from core.log import log
        log.error(f"ChromaDB write skipped: {e}", source="memory")


def clear_memory() -> None:
    """
    Wipe all stored memory facts from SQLite and reset the ChromaDB collection.
    Used by wipeMem.py.
    """
    # 1. Clear SQLite memory entries
    try:
        from memory.db import init_db
        conn = init_db()
        conn.execute("DELETE FROM long_term WHERE key LIKE 'memory:%'")
        conn.commit()
        conn.close()
    except Exception:
        pass

    # 2. Reset ChromaDB collection
    try:
        import chromadb
        from memory.embedder import _CHROMA_DIR, _COLLECTION
        client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        client.delete_collection(_COLLECTION)
        client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception:
        pass   # collection may not exist yet — that's fine


# ── Session logging ───────────────────────────────────────────────────────────

class SessionLogger:
    """
    Writes a structured JSON log file for one run of the agent.

    Ghost session behaviour: no file is created at construction time.
    The first call to log() triggers _anchor(), which creates the file.
    Purely conversational sessions that close without any log() call leave
    no file behind at all.

    Schema:
    {
        "session_id": "2026-04-07_14-32-01",
        "started_at": "2026-04-07T14:32:01Z",
        "ended_at":   null | "2026-04-07T14:45:19Z",
        "turns": [
            {
                "turn":      1,
                "timestamp": "2026-04-07T14:32:05Z",
                "role":      "user",
                "content":   "...",
                "metadata":  {}
            },
            ...
        ]
    }
    """

    def __init__(self) -> None:
        now = datetime.now()
        self._session_id = now.strftime("%Y-%m-%d_%H-%M-%S")
        self._started_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._path: str | None = None   # None = ghost (not yet written to disk)
        self._turn = 0
        self._turns: list[dict] = []

    def log(self, role: str, content: str) -> None:
        """Record a conversation turn.  Anchors to disk on first call."""
        self._turn += 1
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            from mods.passwd.cache import scrub
            content = scrub(content)
        except Exception:
            pass

        self._turns.append({
            "turn":      self._turn,
            "timestamp": ts,
            "role":      role.lower(),
            "content":   content.strip(),
            "metadata":  {},
        })

        if self._path is None:
            self._anchor()
        else:
            self._flush()

    def close(self) -> None:
        """Write ended_at and finalize the log.  No-op for ghost sessions."""
        if self._path is None:
            return
        self._flush(ended_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ"))

    @property
    def path(self) -> str | None:
        """Log file path, or None if this is still a ghost session."""
        return self._path

    @property
    def session_id(self) -> str:
        return self._session_id

    # ── Internals ──────────────────────────────────────────────────────────

    def _anchor(self) -> None:
        """Create the log directory and file on first real log() call."""
        os.makedirs(LOGS_DIR, exist_ok=True)
        self._path = os.path.join(LOGS_DIR, f"{self._session_id}.json")
        self._flush()

    def _flush(self, ended_at: str | None = None) -> None:
        """Write (or overwrite) the JSON log file with current state."""
        data = {
            "session_id": self._session_id,
            "started_at": self._started_at,
            "ended_at":   ended_at,
            "turns":      self._turns,
        }
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
