"""
memory/vault.py — Bucketed knowledge vault.

Each bucket is:
  - A named ChromaDB collection  (vault:<bucket>)       → memory/chroma/  (vectors)
  - A folder on disk             (<VAULT_DIR>/<path>/)   → workspace/vault/ (human-readable)

The folder path is decoupled from the bucket name via index.json. A bucket named
"python-async" can live at "python/async/" in the vault folder hierarchy — Obsidian
sees a clean nested structure while ChromaDB uses flat collection names.

index.json (at workspace/vault/index.json) is maintained by the AGENT, not this
module. The vault module reads it to resolve paths but never writes it. The agent
updates index.json directly using the write/edit skills whenever it creates,
reorganizes, or removes a bucket.

index.json format:
    {
      "updated_at": "2026-04-21T14:32:00",
      "buckets": {
        "python-async": { "path": "python/async", "content_count": 2, ... },
        "project-auth": { "path": "project/auth", "content_count": 1, ... }
      }
    }

The `path` field is relative to VAULT_DIR and can be nested (e.g. "python/async").
If a bucket has no entry in index.json, its path defaults to the bucket name (flat).

Vault layout example:
    workspace/vault/
    ├── index.json
    ├── python/
    │   ├── async/
    │   │   ├── generators.md
    │   │   └── event-loop.md
    │   └── decorators/
    │       └── patterns.md
    └── project/
        └── auth/
            └── token-storage.md

The vault tool only handles what requires ChromaDB (write, delete, query).
Navigation, index.json edits, and bucket reorganisation are done by the agent
directly via shell and file skills — or by the vault organiser agent.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import chromadb
from chromadb import Collection

from config import LOGS_DIR, OLLAMA_EMBED_MODEL, VAULT_DIR

# ── Paths ──────────────────────────────────────────────────────────────────────

_MEMORY_DIR = Path(LOGS_DIR).parent   # memory/
_CHROMA_DIR = _MEMORY_DIR / "chroma"  # memory/chroma/  (vectors stay internal)
_VAULT_DIR  = Path(VAULT_DIR)         # workspace/vault/ (human-readable files)
_INDEX_FILE = _VAULT_DIR / "index.json"

_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# ── Lazy ChromaDB client ───────────────────────────────────────────────────────

_client: chromadb.PersistentClient | None = None
_collections: dict[str, Collection] = {}


def _get_client() -> chromadb.PersistentClient:
    global _client
    if _client is None:
        _CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
    return _client


def _get_bucket_collection(bucket: str) -> Collection:
    if bucket not in _collections:
        _collections[bucket] = _get_client().get_or_create_collection(
            name=f"vault_{bucket}",
            metadata={"hnsw:space": "cosine"},
        )
    return _collections[bucket]


# ── Validation ─────────────────────────────────────────────────────────────────

def _validate_name(value: str, label: str) -> str:
    value = value.strip()
    if not _NAME_RE.match(value):
        raise ValueError(
            f"Invalid {label} '{value}'. "
            "Use only letters, numbers, hyphens, and underscores (max 64 chars)."
        )
    return value


# ── Path resolution via index.json ────────────────────────────────────────────

def _read_index() -> dict:
    """Read index.json. Returns empty structure if missing or unreadable."""
    if not _INDEX_FILE.exists():
        return {"buckets": {}}
    try:
        return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"buckets": {}}


def _resolve_path(bucket: str) -> Path:
    """
    Resolve the filesystem folder for a bucket.

    Reads the `path` field from index.json. If the bucket has no entry,
    falls back to flat placement: VAULT_DIR / bucket.

    This is what decouples ChromaDB collection names from folder hierarchy —
    a bucket named "python-async" can live at "python/async/" in the vault.
    """
    entry = _read_index().get("buckets", {}).get(bucket, {})
    rel   = entry.get("path", "").strip()
    return _VAULT_DIR / rel if rel else _VAULT_DIR / bucket


# ── Public API ─────────────────────────────────────────────────────────────────

def create_bucket(bucket: str) -> str:
    """
    Create the bucket folder and ChromaDB collection.

    Uses the path from index.json if the bucket is already registered there.
    The agent is responsible for updating index.json before or after calling this.
    """
    bucket = _validate_name(bucket, "bucket name")
    folder = _resolve_path(bucket)
    existed = folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    _get_bucket_collection(bucket)
    if existed:
        return f"Bucket '{bucket}' already exists at '{folder.relative_to(_VAULT_DIR)}'."
    return f"Bucket '{bucket}' created at '{folder.relative_to(_VAULT_DIR)}'."


def index_text(bucket: str, doc_id: str, text: str) -> None:
    """
    Upsert plain text into a bucket's ChromaDB collection without writing any file.

    Used by sessions.py so session data stays as JSON on disk while still being
    searchable via RAG.  The doc_id should be unique per record (e.g. 'conv1234').
    """
    bucket = _validate_name(bucket, "bucket name")
    create_bucket(bucket)
    _upsert_content(bucket, doc_id, text.strip())


def delete_index(bucket: str, doc_id: str) -> None:
    """Remove a ChromaDB entry for a session without touching any file."""
    bucket = _validate_name(bucket, "bucket name")
    try:
        _get_bucket_collection(bucket).delete(ids=[f"content:{doc_id}"])
    except Exception:
        pass


def write_content(bucket: str, content: str, body: str) -> str:
    """
    Write (or overwrite) a content entry and re-index it in ChromaDB.

    The folder is resolved from index.json — if the bucket has been moved
    to a nested path by the organiser, writes go to the correct location.
    """
    bucket  = _validate_name(bucket, "bucket name")
    content = _validate_name(content, "content name")
    body    = body.strip()
    if not body:
        raise ValueError("Content body cannot be empty.")

    folder = _resolve_path(bucket)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{content}.md").write_text(body, encoding="utf-8")
    _upsert_content(bucket, content, body)
    return f"Content '{content}' written to '{folder.relative_to(_VAULT_DIR)}' and indexed."


def delete_content(bucket: str, content: str) -> str:
    """Remove a content entry from disk and from the ChromaDB index."""
    bucket  = _validate_name(bucket, "bucket name")
    content = _validate_name(content, "content name")
    path    = _resolve_path(bucket) / f"{content}.md"

    removed = False
    if path.exists():
        path.unlink()
        removed = True

    try:
        _get_bucket_collection(bucket).delete(ids=[f"content:{content}"])
    except Exception:
        pass

    if removed:
        return f"Content '{content}' deleted from bucket '{bucket}'."
    return f"Content '{content}' not found in bucket '{bucket}'."


def query_bucket(
    bucket: str, query: str, top_k: int = 8
) -> list[tuple[str, str, float]]:
    """
    Semantic search within a single bucket.

    Returns (content_name, body, score) tuples sorted by score descending.
    """
    bucket = _validate_name(bucket, "bucket name")
    col    = _get_bucket_collection(bucket)
    if col.count() == 0:
        return []

    embedding = _embed(query)
    results   = col.query(
        query_embeddings=[embedding],
        n_results=min(top_k, col.count()),
        include=["documents", "distances", "metadatas"],
    )

    output: list[tuple[str, str, float]] = []
    for doc, dist, meta in zip(
        results["documents"][0],
        results["distances"][0],
        results["metadatas"][0],
    ):
        score = max(0.0, 1.0 - dist)
        if score < 0.3:
            continue
        output.append((meta.get("content", ""), doc, round(score, 4)))
    return output


def query_all(query: str, top_k: int = 5) -> list[tuple[str, str, str, float]]:
    """
    Semantic search across every bucket registered in index.json.

    Returns (bucket, content_name, body, score) tuples sorted by score descending.
    Falls back to disk scan if index.json has no buckets.
    """
    results: list[tuple[str, str, str, float]] = []
    for bucket in list_buckets():
        for content_name, body, score in query_bucket(bucket, query, top_k=top_k):
            results.append((bucket, content_name, body, score))
    results.sort(key=lambda x: x[3], reverse=True)
    return results


def list_buckets() -> list[str]:
    """
    Return sorted list of bucket names.

    Prefers index.json (agent-maintained). Falls back to disk scan so the
    tool still works before index.json is written.
    """
    index_buckets = list(_read_index().get("buckets", {}).keys())
    if index_buckets:
        return sorted(index_buckets)
    if not _VAULT_DIR.exists():
        return []
    return sorted(p.name for p in _VAULT_DIR.iterdir() if p.is_dir())


def list_contents(bucket: str) -> list[str]:
    """Return sorted list of content names (filename stems) in a bucket."""
    bucket = _validate_name(bucket, "bucket name")
    folder = _resolve_path(bucket)
    if not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.md"))


def reindex_all_buckets(skip_if_indexed: bool = True) -> str:
    """
    Reindex every registered bucket from disk.

    If skip_if_indexed=True, skips buckets whose ChromaDB collection already
    has entries — only fills empty collections, so it's safe to call on startup.
    Returns a summary string suitable for logging.
    """
    buckets = list_buckets()
    if not buckets:
        return "No vault buckets registered — nothing to reindex."

    indexed, skipped, failed = 0, 0, []
    for bucket in buckets:
        try:
            if skip_if_indexed:
                col = _get_bucket_collection(bucket)
                if col.count() > 0:
                    skipped += 1
                    continue
            reindex_bucket(bucket)
            indexed += 1
        except Exception as e:
            failed.append(f"{bucket}: {e}")

    parts = []
    if indexed:
        parts.append(f"{indexed} bucket(s) indexed")
    if skipped:
        parts.append(f"{skipped} skipped (already indexed)")
    if failed:
        parts.append(f"{len(failed)} failed: " + ", ".join(failed))
    return "Vault reindex: " + "; ".join(parts) if parts else "Nothing to do."


def reindex_bucket(bucket: str) -> str:
    """
    Re-embed every content entry in a bucket from disk.

    Resolves the folder via index.json — so if the organiser moved the bucket
    to a new path and updated index.json, reindex picks up the new location.
    """
    bucket   = _validate_name(bucket, "bucket name")
    contents = list_contents(bucket)
    if not contents:
        return f"Bucket '{bucket}' is empty — nothing to reindex."

    count  = 0
    errors: list[str] = []
    for content in contents:
        try:
            body = ((_resolve_path(bucket)) / f"{content}.md").read_text(encoding="utf-8")
            _upsert_content(bucket, content, body)
            count += 1
        except Exception as e:
            errors.append(f"{content}: {e}")

    msg = f"Reindexed {count}/{len(contents)} entries in bucket '{bucket}'."
    if errors:
        msg += "\nErrors:\n" + "\n".join(f"  {e}" for e in errors)
    return msg


# ── Internal helpers ──────────────────────────────────────────────────────────

def _upsert_content(bucket: str, content: str, body: str) -> None:
    col    = _get_bucket_collection(bucket)
    doc_id = f"content:{content}"
    meta   = {
        "bucket":     bucket,
        "content":    content,
        "updated_at": datetime.now().isoformat(),
        "length":     len(body),
    }
    # nomic-embed-text has an ~8k token context, but code-heavy docs tokenize
    # more densely.  Cap at 4 000 chars (~1 000 tokens) to be safe across all
    # content types.  The full body is stored as the document so retrieval
    # always returns complete content.
    embedding = _embed(body[:4000])
    if col.get(ids=[doc_id])["ids"]:
        col.update(ids=[doc_id], embeddings=[embedding], documents=[body], metadatas=[meta])
    else:
        col.add(ids=[doc_id], embeddings=[embedding], documents=[body], metadatas=[meta])


def _embed(text: str) -> list[float]:
    import ollama
    response = ollama.embeddings(model=OLLAMA_EMBED_MODEL, prompt=text)
    return response["embedding"]
