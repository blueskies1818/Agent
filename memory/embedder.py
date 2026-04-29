"""
memory/embedder.py — Embed text and persist into ChromaDB.

Responsible for:
  - Generating embeddings via Ollama (nomic-embed-text, local, no API key)
  - Storing (text, embedding, metadata) in a persistent ChromaDB collection
  - Deduplicating identical content so the same fact isn't stored twice

ChromaDB stores its data in memory/chroma/ as a plain directory.
No server needed — it runs fully embedded in-process.

Prerequisite: Ollama running locally (`ollama serve`) with the model pulled:
    ollama pull nomic-embed-text
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb import Collection

from config import LOGS_DIR, OLLAMA_EMBED_MODEL

# ── Paths ─────────────────────────────────────────────────────────────────────

_MEMORY_DIR        = Path(LOGS_DIR).parent          # memory/
_CHROMA_DIR        = _MEMORY_DIR / "chroma"         # memory/chroma/
_COLLECTION        = "agent_memory"
_SKILLS_COLLECTION = "agent_skills"


# ── Lazy singletons ───────────────────────────────────────────────────────────

_chroma_client: chromadb.PersistentClient | None = None
_collection: Collection | None = None
_skills_collection: Collection | None = None


def _get_client() -> chromadb.PersistentClient:
    global _chroma_client
    if _chroma_client is None:
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
    return _chroma_client


def _get_collection() -> Collection:
    global _collection
    if _collection is None:
        _collection = _get_client().get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},   # cosine similarity for text
        )
    return _collection


def _get_skills_collection() -> Collection:
    global _skills_collection
    if _skills_collection is None:
        _skills_collection = _get_client().get_or_create_collection(
            name=_SKILLS_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    return _skills_collection


# ── Public API ─────────────────────────────────────────────────────────────────

def embed_and_store(text: str, metadata: dict | None = None) -> str:
    """
    Embed text and store it in ChromaDB.

    Returns the document ID. Duplicate content (same hash) is skipped
    and the existing ID returned.

    Args:
        text:     The text to embed and store.
        metadata: Optional dict stored alongside the vector
                  (e.g. {"source": "agent", "turn": 5}).
    """
    text = text.strip()
    if not text:
        raise ValueError("Cannot embed empty text.")

    doc_id = _content_hash(text)
    col    = _get_collection()

    # Skip if already stored
    existing = col.get(ids=[doc_id])
    if existing["ids"]:
        return doc_id

    embedding = _embed(text)
    meta = {
        "timestamp": datetime.now().isoformat(),
        "length":    len(text),
        **(metadata or {}),
    }

    col.add(
        ids=[doc_id],
        embeddings=[embedding],
        documents=[text],
        metadatas=[meta],
    )
    return doc_id


def embed_conversation_turn(user: str, assistant: str, metadata: dict | None = None) -> str:
    """
    Embed a user+assistant exchange as a single document.

    Stored as a formatted pair so retrieval surfaces the full exchange,
    not just one side of it.
    """
    text = f"User: {user.strip()}\nAssistant: {assistant.strip()}"
    meta = {"source": "conversation", **(metadata or {})}
    return embed_and_store(text, metadata=meta)


def embed_skill(name: str, description: str, content: str, metadata: dict | None = None) -> str:
    """
    Embed a skill file and store it in the agent_skills ChromaDB collection.

    Uses the skill name as the stable document ID so re-registering a skill
    overwrites the previous embedding without creating duplicates.

    Args:
        name:        Skill name (used as document ID — e.g. "ffmpeg").
        description: One-line description used for semantic search.
        content:     Full skill file content (stored for retrieval, but
                     indexed by description for Phase 1 hint retrieval).
        metadata:    Optional extra metadata fields.
    """
    col   = _get_skills_collection()
    doc_id = f"skill:{name}"

    # Index text = description + full content for rich semantic matching
    index_text = f"{name}: {description}\n\n{content}".strip()
    embedding  = _embed(index_text)

    meta = {
        "name":        name,
        "description": description,
        "timestamp":   datetime.now().isoformat(),
        **(metadata or {}),
    }

    existing = col.get(ids=[doc_id])
    if existing["ids"]:
        col.update(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )
    else:
        col.add(
            ids=[doc_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[meta],
        )
    return doc_id


def remove(doc_id: str) -> None:
    """Remove a specific document by ID from the memory collection."""
    _get_collection().delete(ids=[doc_id])


def remove_skill(name: str) -> None:
    """Remove a skill from the skills collection by skill name."""
    _get_skills_collection().delete(ids=[f"skill:{name}"])


def count() -> int:
    """Return total number of stored memory entries."""
    return _get_collection().count()


def skill_count() -> int:
    """Return total number of embedded skills."""
    return _get_skills_collection().count()


# ── Internals ─────────────────────────────────────────────────────────────────

def _embed(text: str) -> list[float]:
    """Generate an embedding vector for text using Ollama (local, no API key)."""
    import ollama
    response = ollama.embeddings(model=OLLAMA_EMBED_MODEL, prompt=text)
    return response["embedding"]


def _content_hash(text: str) -> str:
    """Stable ID derived from content — ensures deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]
