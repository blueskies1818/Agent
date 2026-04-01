"""
mods/memory/memory.py — Persistent memory command.

Intercepted shell syntax:
    memory -query "what do i know about PyQt6"
    memory -read
    memory -write "user prefers dark mode"
    memory -prefs
    memory -pref user_name Alice
    memory -blobs
    memory -blobs tags=memory,sqlite
    memory -blob build_config

The mod searches across all memory stores:
  - conversation history (task_summary, plan_record, compression entries)
  - blob_index (completed task metadata)
  - long_term preferences (key-value pairs)
  - flat memory.txt file (legacy)
  - ChromaDB / RAG retriever (if available)
"""

from __future__ import annotations

import sqlite3

from mods._shared import extract_quoted as _extract_quoted

NAME        = "memory"
DESCRIPTION = "Query, read, or write persistent memory across sessions"


def handle(args: list[str], raw: str) -> str:
    """Dispatch to the appropriate memory operation based on the first flag."""
    if not args:
        return _usage()

    flag = args[0].lower().lstrip("-")

    if flag == "query":
        query = _extract_quoted(args[1:], raw, "-query")
        if not query:
            return "[ERROR] memory -query requires a search string.\n" + _usage()
        return _query(query)

    elif flag == "read":
        return _read_flat()

    elif flag == "write":
        content = _extract_quoted(args[1:], raw, "-write")
        if not content:
            return "[ERROR] memory -write requires content.\n" + _usage()
        return _write_flat(content)

    elif flag == "prefs":
        return _list_prefs()

    elif flag == "pref" and len(args) >= 3:
        key = args[1]
        value = " ".join(args[2:])
        return _set_pref(key, value)

    elif flag == "blobs":
        kwargs = _parse_blob_filters(args[1:])
        return _list_blobs(**kwargs)

    elif flag == "blob" and len(args) >= 2:
        return _read_blob(args[1])

    else:
        return f"[ERROR] Unknown memory operation: '{flag}'\n" + _usage()


def _parse_blob_filters(args: list[str]) -> dict:
    """Parse key=value pairs from blob list args."""
    filters: dict = {}
    for a in args:
        if "=" in a:
            k, v = a.split("=", 1)
            filters[k.strip()] = v.strip()
    return filters


# ── DB connection helper ──────────────────────────────────────────────────────

def _get_db() -> sqlite3.Connection | None:
    from memory.db import get_db
    return get_db()


# ── Operations ────────────────────────────────────────────────────────────────

def _query(query: str) -> str:
    """
    Search across all memory stores for relevant information.

    Search order:
      1. Long-term preferences (exact key match)
      2. Blob index (keyword search in name + summary)
      3. Conversation history (keyword search in summaries + compressions)
      4. ChromaDB / RAG retriever (semantic, if available)
      5. Flat memory.txt (keyword search, legacy fallback)
    """
    results: list[str] = []

    conn = _get_db()

    # 1. Long-term preferences
    if conn:
        try:
            from memory.long_term import get_all
            prefs = get_all(conn)
            query_lower = query.lower()
            matched = [
                f"  {p['key']}: {p['value']}"
                for p in prefs
                if query_lower in p["key"].lower() or query_lower in p["value"].lower()
            ]
            if matched:
                results.append("── Preferences ──\n" + "\n".join(matched))
        except Exception:
            pass

    # 2. Blob index
    if conn:
        try:
            from memory.task_blobs import query_index
            hits = query_index(conn, keyword=query, days_back=365)
            if hits:
                lines = [f"  {h['name']} — {h['summary']} [{h['date']}]" for h in hits[:10]]
                results.append("── Task blobs ──\n" + "\n".join(lines))
        except Exception:
            pass

    # 3. Conversation history (summaries and compressions)
    if conn:
        try:
            keyword = f"%{query}%"
            rows = conn.execute(
                """SELECT entry_type, content, date, created_at
                   FROM conversation
                   WHERE entry_type IN ('task_summary', 'plan_record', 'compression', 'trivial_summary')
                     AND content LIKE ?
                   ORDER BY created_at DESC
                   LIMIT 10""",
                (keyword,),
            ).fetchall()
            if rows:
                lines = []
                for r in rows:
                    content = r["content"]
                    if len(content) > 200:
                        content = content[:197] + "..."
                    date = r["date"] or ""
                    lines.append(f"  [{r['entry_type']}] {content} {f'[{date}]' if date else ''}")
                results.append("── Conversation history ──\n" + "\n".join(lines))
        except Exception:
            pass

    # 4. ChromaDB / RAG retriever (semantic search)
    try:
        from memory.rag import MemoryRetriever
        from config import RAG_MIN_SCORE, RAG_TOP_K
        retriever = MemoryRetriever(min_score=RAG_MIN_SCORE)
        rag_hits = retriever.retrieve(query, top_k=RAG_TOP_K)
        if rag_hits:
            lines = [f"  [similarity {score:.2f}] {text}" for text, score in rag_hits]
            results.append("── Semantic memory (RAG) ──\n" + "\n".join(lines))
    except Exception:
        pass

    # 5. Flat memory.txt fallback
    try:
        from config import MEMORY_FILE
        from pathlib import Path
        mem_path = Path(MEMORY_FILE)
        if mem_path.exists():
            content = mem_path.read_text(encoding="utf-8").strip()
            if content:
                query_lower = query.lower()
                matched_lines = [
                    f"  {line.strip()}"
                    for line in content.splitlines()
                    if line.strip() and query_lower in line.lower()
                ]
                if matched_lines:
                    results.append("── memory.txt ──\n" + "\n".join(matched_lines[:10]))
    except Exception:
        pass

    if not results:
        return f"(no memories found matching '{query}')"

    return f"Memory results for '{query}':\n\n" + "\n\n".join(results)


def _read_flat() -> str:
    """Read the full flat memory file."""
    try:
        from config import MEMORY_FILE
        from pathlib import Path
        path = Path(MEMORY_FILE)
        if path.exists():
            content = path.read_text(encoding="utf-8").strip()
            return content if content else "(memory.txt is empty)"
        return "(memory.txt does not exist)"
    except Exception as e:
        return f"[ERROR] Could not read memory: {e}"


def _write_flat(content: str) -> str:
    """Write a fact to both flat file and ChromaDB."""
    try:
        from memory.memory import write_memory
        write_memory(content)
        return f"Memory written: {content}"
    except Exception:
        try:
            from config import MEMORY_FILE
            from pathlib import Path
            path = Path(MEMORY_FILE)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(content + "\n")
            return f"Memory written (flat file only): {content}"
        except Exception as e:
            return f"[ERROR] Could not write memory: {e}"


def _list_prefs() -> str:
    """List all long-term preferences."""
    conn = _get_db()
    if not conn:
        return "(database unavailable — no preferences stored)"
    try:
        from memory.long_term import format_for_injection
        text = format_for_injection(conn)
        return text if text else "(no preferences set)"
    except Exception as e:
        return f"[ERROR] {e}"


def _set_pref(key: str, value: str) -> str:
    """Set a long-term preference."""
    conn = _get_db()
    if not conn:
        return "[ERROR] Database unavailable."
    try:
        from memory.long_term import set as lt_set
        lt_set(conn, key, value)
        return f"Preference saved: {key} = {value}"
    except Exception as e:
        return f"[ERROR] {e}"


def _list_blobs(**kwargs) -> str:
    """List recent task blobs from the index."""
    conn = _get_db()
    if not conn:
        return "(database unavailable — no blobs stored)"
    try:
        from memory.task_blobs import query_index
        hits = query_index(
            conn,
            tags=kwargs.get("tags"),
            keyword=kwargs.get("keyword"),
            days_back=int(kwargs.get("days", 30)),
        )
        if not hits:
            return "(no task blobs found)"
        lines = [f"  {h['name']} — {h['summary']} [{h['date']}]" for h in hits]
        return "Task blobs:\n" + "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


def _read_blob(name: str) -> str:
    """Load full blob content by name."""
    conn = _get_db()
    if not conn:
        return "[ERROR] Database unavailable."
    try:
        from memory.task_blobs import read_blob
        content = read_blob(conn, name)
        if content:
            return content
        return f"(no blob found with name '{name}')"
    except Exception as e:
        return f"[ERROR] {e}"


# ── Usage ─────────────────────────────────────────────────────────────────────

def _usage() -> str:
    return """Usage:
  memory -query "search terms"       Search all memory stores
  memory -read                       Read flat memory file
  memory -write "fact to remember"   Persist a fact
  memory -prefs                      List long-term preferences
  memory -pref key value             Set a preference
  memory -blobs                      List recent task blobs
  memory -blobs tags=memory,sqlite   Filter blobs by tag
  memory -blob blob_name             Load full blob content"""