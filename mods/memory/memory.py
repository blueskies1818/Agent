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
    memory -vault skills "how do I create a skill"
    memory -vault * "async patterns"

The mod searches across all memory stores:
  - conversation history (task_summary, plan_record, compression entries)
  - blob_index (completed task metadata)
  - long_term preferences (key-value pairs)
  - ChromaDB / RAG retriever (semantic embeddings)
  - vault bucket collections (semantic search over knowledge docs)
"""

from __future__ import annotations

import sqlite3

from mods._shared import extract_quoted as _extract_quoted


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

    elif flag == "vault":
        if len(args) < 3:
            return "[ERROR] memory -vault requires <bucket|*> and a search string.\n" + _usage()
        bucket = args[1]
        query  = _extract_quoted(args[2:], raw, f"-vault {bucket}")
        if not query:
            query = " ".join(args[2:]).strip("\"'")
        if not query:
            return "[ERROR] memory -vault requires a search string.\n" + _usage()
        return _query_vault(bucket, query)

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

    elif flag == "sessions":
        limit = int(args[1]) if len(args) >= 2 and args[1].isdigit() else 20
        return _list_sessions(limit)

    elif flag == "session" and len(args) >= 2:
        return _load_session(args[1])

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
      5. Vault bucket collections (semantic search across all buckets)
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
        from config import RAG_MIN_SCORE, RAG_CANDIDATE_K
        retriever = MemoryRetriever(min_score=RAG_MIN_SCORE)
        rag_hits = retriever.retrieve(query, top_k=RAG_CANDIDATE_K)
        if rag_hits:
            lines = [f"  [similarity {score:.2f}] {text}" for text, score in rag_hits]
            results.append("── Semantic memory (RAG) ──\n" + "\n".join(lines))
    except Exception:
        pass

    # 5. Vault bucket collections
    try:
        from memory.vault import query_all
        vault_hits = query_all(query, top_k=3)
        if vault_hits:
            lines = [
                f"  [{score:.2f}] {b}/{c} — {_truncate(body)}"
                for b, c, body, score in vault_hits
            ]
            results.append("── Vault knowledge ──\n" + "\n".join(lines))
    except Exception:
        pass

    if not results:
        return f"(no memories found matching '{query}')"

    return f"Memory results for '{query}':\n\n" + "\n\n".join(results)


def _query_vault(bucket: str, query: str) -> str:
    """Semantic search within a specific vault bucket, or all buckets if bucket is '*'."""
    try:
        if bucket == "*":
            from memory.vault import query_all
            hits = query_all(query)
            if not hits:
                return f"(no vault content matching '{query}')"
            lines = [
                f"  [{score:.2f}] {b}/{c}\n    {_truncate(body)}"
                for b, c, body, score in hits
            ]
            return f"Vault search (all buckets) for '{query}':\n\n" + "\n\n".join(lines)
        else:
            from memory.vault import query_bucket
            hits = query_bucket(bucket, query)
            if not hits:
                return f"(no content in bucket '{bucket}' matching '{query}')"
            lines = [
                f"  [{score:.2f}] {c}\n    {_truncate(body)}"
                for c, body, score in hits
            ]
            return f"Vault '{bucket}' results for '{query}':\n\n" + "\n\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


def _read_flat() -> str:
    """Read recent long-term memory entries from SQLite."""
    conn = _get_db()
    if not conn:
        return "(database unavailable — no memory stored)"
    try:
        rows = conn.execute(
            """SELECT content, created_at FROM long_term
               ORDER BY created_at DESC LIMIT 50"""
        ).fetchall()
        if not rows:
            return "(no long-term memories stored)"
        lines = [f"  [{r['created_at'][:10]}] {r['content']}" for r in rows]
        return "Long-term memory:\n" + "\n".join(lines)
    except Exception as e:
        return f"[ERROR] Could not read memory: {e}"


def _write_flat(content: str) -> str:
    """Write a fact to SQLite and ChromaDB."""
    try:
        from memory.memory import write_memory
        write_memory(content)
        return f"Memory written: {content}"
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


def _list_sessions(limit: int = 20) -> str:
    """List recent sessions from SQLite."""
    try:
        from memory.sessions import list_sessions
        sessions = list_sessions(limit)
        if not sessions:
            return "(no past sessions recorded)"
        lines = []
        for s in sessions:
            sid     = s.get("id", "?")
            started = (s.get("started_at") or "")[:16].replace("T", " ")
            ended   = (s.get("ended_at")   or "")[:16].replace("T", " ")
            summary = s.get("summary") or ""
            if summary:
                summary = "  — " + summary[:100]
            lines.append(f"  {sid}  ({started} → {ended}){summary}")
        return f"Past sessions ({len(sessions)}):\n" + "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


def _load_session(session_id: str) -> str:
    """Load a session's vault entry, falling back to raw turns if not in vault."""
    # Try vault first (has full Markdown)
    try:
        from pathlib import Path
        from config import VAULT_DIR
        md_path = Path(VAULT_DIR) / "sessions" / f"{session_id}.md"
        if md_path.exists():
            return md_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # Fallback: reconstruct from SQLite turns
    try:
        from memory.sessions import load_session_turns
        turns = load_session_turns(session_id)
        if not turns:
            return f"(no session found with id '{session_id}')"
        lines = [f"# Session {session_id}", ""]
        for t in turns:
            role    = (t.get("role") or "?").capitalize()
            content = (t.get("content") or "").strip()
            lines.append(f"**{role}**: {content}")
            lines.append("")
        return "\n".join(lines)
    except Exception as e:
        return f"[ERROR] {e}"


def _truncate(text: str, limit: int = 120) -> str:
    return (text.replace("\n", " ").strip()[:limit] + "...") if len(text) > limit else text.replace("\n", " ").strip()


# ── Usage ─────────────────────────────────────────────────────────────────────

def _usage() -> str:
    return """Usage:
  memory -query "search terms"              Search all memory stores + vault
  memory -vault <bucket> "search terms"     Semantic search within a vault bucket
  memory -vault * "search terms"            Semantic search across ALL vault buckets
  memory -read                              Read long-term memory entries
  memory -write "fact to remember"          Persist a fact
  memory -prefs                             List long-term preferences
  memory -pref key value                    Set a preference
  memory -blobs                             List recent task blobs
  memory -blobs tags=memory,sqlite          Filter blobs by tag
  memory -blob blob_name                    Load full blob content
  memory -sessions                          List past sessions (newest first)
  memory -sessions 5                        List last 5 sessions
  memory -session <session_id>              Load full conversation for a session"""
