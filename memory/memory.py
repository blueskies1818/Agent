"""
memory/memory.py — Persistent memory and session logging.

Memory model:
  memory/memory.txt    -> flat text fallback (human-readable, always written)
  memory/chroma/       -> ChromaDB vector store (semantic retrieval)
  memory/logs/         -> per-session turn transcripts (auto-written by loop)

write_memory() writes to both stores so the system can always fall back
to reading memory.txt if ChromaDB is unavailable, and can do semantic
search when it is available.
"""

import os
from datetime import datetime

from config import MEMORY_FILE, LOGS_DIR


# ── Ensure directories exist ──────────────────────────────────────────────────

def _ensure_dirs() -> None:
    os.makedirs(os.path.dirname(MEMORY_FILE), exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)


# ── Persistent memory ─────────────────────────────────────────────────────────

def read_memory() -> str:
    """Return contents of memory.txt, or empty string if it doesn't exist."""
    _ensure_dirs()
    if not os.path.exists(MEMORY_FILE):
        return ""
    with open(MEMORY_FILE, "r", encoding="utf-8") as f:
        return f.read().strip()


def write_memory(content: str) -> None:
    """
    Persist a fact to both memory.txt (flat) and ChromaDB (vector).

    ChromaDB embedding is attempted but never blocks the write — if the
    OpenAI API is unavailable the fact is still saved to memory.txt.
    """
    _ensure_dirs()
    content = content.strip()
    if not content:
        return

    # 1. Always write to flat file
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"\n[{timestamp}]\n{content}\n"
    with open(MEMORY_FILE, "a", encoding="utf-8") as f:
        f.write(entry)

    # 2. Best-effort embed into ChromaDB
    try:
        from memory.embedder import embed_and_store
        embed_and_store(content, metadata={"source": "agent", "timestamp": timestamp})
    except Exception as e:
        # Silently degrade — flat file is the source of truth
        import sys
        print(f"[memory] ChromaDB write skipped: {e}", file=sys.stderr)


def clear_memory() -> None:
    """
    Wipe memory.txt and reset the ChromaDB collection.
    Used by wipeClean.py.
    """
    _ensure_dirs()
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        f.write("")

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
    """Writes a structured log file for one run of the agent."""

    def __init__(self) -> None:
        _ensure_dirs()
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._path = os.path.join(LOGS_DIR, f"{ts}.log")
        self._turn = 0
        self._write(f"=== SESSION START {ts} ===\n")

    def log(self, role: str, content: str) -> None:
        self._turn += 1
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            from mods.passwd.cache import scrub
            content = scrub(content)
        except Exception:
            pass
        block = (
            f"\n--- Turn {self._turn} | {role} | {ts} ---\n"
            f"{content.strip()}\n"
        )
        self._write(block)

    def close(self) -> None:
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self._write(f"\n=== SESSION END {ts} ===\n")

    def _write(self, text: str) -> None:
        with open(self._path, "a", encoding="utf-8") as f:
            f.write(text)

    @property
    def path(self) -> str:
        return self._path