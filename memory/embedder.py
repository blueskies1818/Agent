"""
memory/embedder.py — Embed text and persist into ChromaDB.

Responsible for:
  - Generating embeddings via OpenAI text-embedding-3-small
  - Storing (text, embedding, metadata) in a persistent ChromaDB collection
  - Deduplicating identical content so the same fact isn't stored twice

ChromaDB stores its data in memory/chroma/ as a plain directory.
No server needed — it runs fully embedded in-process.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb import Collection
from openai import OpenAI

from config import LOGS_DIR  # reuse base dir derivation

# ── Paths ─────────────────────────────────────────────────────────────────────

_MEMORY_DIR  = Path(LOGS_DIR).parent          # memory/
_CHROMA_DIR  = _MEMORY_DIR / "chroma"         # memory/chroma/
_COLLECTION  = "agent_memory"
_EMBED_MODEL = "text-embedding-3-small"


# ── Lazy singletons ───────────────────────────────────────────────────────────

_chroma_client: chromadb.PersistentClient | None = None
_collection: Collection | None = None
_openai_client: OpenAI | None = None


def _get_collection() -> Collection:
    global _chroma_client, _collection
    if _collection is None:
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        _collection = _chroma_client.get_or_create_collection(
            name=_COLLECTION,
            metadata={"hnsw:space": "cosine"},   # cosine similarity for text
        )
    return _collection


def _get_openai() -> OpenAI:
    global _openai_client
    if _openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is not set.")
        _openai_client = OpenAI(api_key=api_key)
    return _openai_client


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


def remove(doc_id: str) -> None:
    """Remove a specific document by ID."""
    _get_collection().delete(ids=[doc_id])


def count() -> int:
    """Return total number of stored memory entries."""
    return _get_collection().count()


# ── Internals ─────────────────────────────────────────────────────────────────

def _embed(text: str) -> list[float]:
    """Generate an embedding vector for text using OpenAI."""
    response = _get_openai().embeddings.create(
        model=_EMBED_MODEL,
        input=text,
    )
    return response.data[0].embedding


def _content_hash(text: str) -> str:
    """Stable ID derived from content — ensures deduplication."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]