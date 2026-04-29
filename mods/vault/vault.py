"""
mods/vault/vault.py — Vault navigation and maintenance.

Intercepted shell syntax:
    vault -list
    vault -contents <bucket>
    vault -reindex               (reindex ALL buckets)
    vault -reindex  <bucket>     (reindex one bucket)

Reading vault content directly — use the read/cat shell skill:
    cat workspace/vault/index.json
    cat workspace/vault/internals/skills/skill-read.md

Semantic search of vault buckets — use the memory mod:
    memory -vault skills "how do I create a skill"
    memory -vault * "async patterns"
"""

from __future__ import annotations


def handle(args: list[str], raw: str) -> str:
    if not args:
        return _usage()

    flag = args[0].lower().lstrip("-")

    if flag == "list":
        return _list_buckets()

    elif flag == "contents":
        if len(args) < 2:
            return "[ERROR] vault -contents requires a bucket name.\n" + _usage()
        return _list_contents(args[1])

    elif flag == "reindex":
        bucket = args[1] if len(args) >= 2 else None
        return _reindex(bucket)

    else:
        return f"[ERROR] Unknown vault operation: '{flag}'\n" + _usage()


# ── Operations ────────────────────────────────────────────────────────────────

def _list_buckets() -> str:
    try:
        from memory.vault import list_buckets, _read_index
        buckets = list_buckets()
        if not buckets:
            return "(no vault buckets registered in index.json)"
        index = _read_index()
        lines = []
        for b in buckets:
            entry = index.get("buckets", {}).get(b, {})
            path  = entry.get("path", b)
            desc  = entry.get("description", "")
            count = entry.get("content_count", "?")
            lines.append(f"  {b}  ({count} docs)  →  {path}/\n    {desc}")
        return "Vault buckets:\n\n" + "\n\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


def _list_contents(bucket: str) -> str:
    try:
        from memory.vault import list_contents, _resolve_path
        contents = list_contents(bucket)
        if not contents:
            return f"(bucket '{bucket}' is empty or does not exist)"
        folder = _resolve_path(bucket)
        lines = [f"  {c}.md  →  {folder}/{c}.md" for c in contents]
        return f"Contents in '{bucket}' ({len(contents)} docs):\n" + "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


def _reindex(bucket: str | None) -> str:
    try:
        if bucket is None:
            from memory.vault import reindex_all_buckets
            return reindex_all_buckets(skip_if_indexed=False)
        from memory.vault import reindex_bucket
        return reindex_bucket(bucket)
    except Exception as e:
        return f"[ERROR] {e}"


# ── Usage ─────────────────────────────────────────────────────────────────────

def _usage() -> str:
    return """Usage:
  vault -list                  Show all buckets (from index.json)
  vault -contents <bucket>     List docs in a bucket with their file paths
  vault -reindex               Re-embed ALL buckets from disk
  vault -reindex  <bucket>     Re-embed one bucket from disk

To read vault docs directly:
  cat workspace/vault/index.json
  cat workspace/vault/<path>/<doc>.md

To search vault buckets semantically:
  memory -vault <bucket> "query"
  memory -vault * "query"       (search all buckets)"""
