"""
memory/rag.py — Semantic retriever over ChromaDB.

Implements the core.prompt_evaluator.Retriever protocol so it can be
passed directly into PromptEvaluator without any other changes.

Usage:
    from memory.rag import MemoryRetriever

    retriever = MemoryRetriever()
    results = retriever.retrieve("how do I write Python files?")
    for content, score in results:
        print(f"{score:.2f}  {content[:80]}")

Token budget retrieval (V2):
    Instead of a fixed top-k count, retrieve() fetches up to `top_k` candidates
    from ChromaDB (RAG_CANDIDATE_K), then greedily adds results until the next
    one would push the running token count over RAG_TOKEN_BUDGET.  This prevents
    any single retrieval from flooding the context window regardless of document size.
"""

from __future__ import annotations

from pathlib import Path

from config import RAG_TOKEN_BUDGET, SKILL_TOKEN_BUDGET, SKILLS_DIR, VAULT_TOKEN_BUDGET
from core.context_window import _count_tokens
from memory.embedder import _embed, _get_collection, _get_skills_collection, embed_skill


class MemoryRetriever:
    """
    Semantic search over the ChromaDB memory collection.

    Satisfies the core.prompt_evaluator.Retriever protocol:
        retrieve(query, top_k) -> list[tuple[str, float]]

    The `top_k` argument controls how many candidates to pull from ChromaDB
    (the candidate pool), not the final return count.  The actual number of
    results returned is governed by RAG_TOKEN_BUDGET — results are added
    greedily until the next one would exceed the budget.

    Scores are cosine similarities in [0.0, 1.0].
    ChromaDB returns distances (lower = more similar), so we convert:
        score = 1 - distance
    """

    def __init__(self, min_score: float = 0.0) -> None:
        """
        Args:
            min_score: Filter out results below this similarity threshold.
                       0.0 keeps everything; 0.5 keeps only close matches.
        """
        self._min_score = min_score

    def retrieve(self, query: str, top_k: int = 10) -> list[tuple[str, float]]:
        """
        Embed the query and return the most relevant stored facts that fit
        within RAG_TOKEN_BUDGET.

        Args:
            query:  Natural language query string.
            top_k:  Candidate pool size — how many results to request from
                    ChromaDB before budget filtering.  Defaults to 10
                    (RAG_CANDIDATE_K).

        Returns:
            List of (content, score) tuples, sorted by score descending.
            Empty list if the collection has no entries or nothing passes
            min_score and the token budget.
        """
        col = _get_collection()
        if col.count() == 0:
            return []

        # Cap candidate count at what's actually stored
        n = min(top_k, col.count())

        embedding = _embed(query)
        results = col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["documents", "distances"],
        )

        docs      = results["documents"][0]      # list of strings
        distances = results["distances"][0]       # list of floats (0=identical)

        output: list[tuple[str, float]] = []
        token_count = 0

        for doc, dist in zip(docs, distances):
            score = max(0.0, 1.0 - dist)         # convert distance → similarity
            if score < self._min_score:
                continue

            doc_tokens = _count_tokens(doc)
            if token_count + doc_tokens > RAG_TOKEN_BUDGET:
                break                             # budget exhausted — stop adding

            token_count += doc_tokens
            output.append((doc, round(score, 4)))

        # Already sorted by distance ascending = score descending
        return output


# ── Skill retriever ───────────────────────────────────────────────────────────

class SkillRetriever:
    """
    Semantic search over the ChromaDB skills collection (agent_skills).

    Provides Phase 1 passive skill injection: retrieve_hints() returns
    (name, description, score) tuples — name and description only, never
    full skill content.  Token budget capped via SKILL_TOKEN_BUDGET.

    Bootstrap: if the skills collection is empty on first use, indexes all
    .md files found in SKILLS_DIR so the library is immediately searchable.
    Skills added manually to SKILLS_DIR are picked up on the next session start,
    so the bootstrap only runs once on a fresh install.
    """

    def __init__(self, min_score: float = 0.0) -> None:
        self._min_score    = min_score
        self._bootstrapped = False

    def _ensure_bootstrapped(self) -> None:
        if self._bootstrapped:
            return
        self._bootstrapped = True
        col = _get_skills_collection()
        if col.count() > 0:
            return
        _index_skills_dir(SKILLS_DIR)

    def retrieve_hints(
        self, query: str, top_k: int = 5
    ) -> list[tuple[str, str, float]]:
        """
        Return the most semantically relevant skills for query as hints.

        Returns:
            List of (name, description, score) tuples, sorted by score
            descending, capped by SKILL_TOKEN_BUDGET (using hint text size).
            Empty list if the collection is empty or no results pass min_score.
        """
        self._ensure_bootstrapped()
        col = _get_skills_collection()
        if col.count() == 0:
            return []

        n         = min(top_k, col.count())
        embedding = _embed(query)
        results   = col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["distances", "metadatas"],
        )

        distances = results["distances"][0]
        metas     = results["metadatas"][0]

        output: list[tuple[str, str, float]] = []
        token_count = 0

        for dist, meta in zip(distances, metas):
            score = max(0.0, 1.0 - dist)
            if score < self._min_score:
                continue

            name        = meta.get("name", "")
            description = meta.get("description", "")
            hint_text   = f"Skill available: '{name}' — {description}"
            hint_tokens = _count_tokens(hint_text)

            if token_count + hint_tokens > SKILL_TOKEN_BUDGET:
                break

            token_count += hint_tokens
            output.append((name, description, round(score, 4)))

        return output


# ── Vault retriever ───────────────────────────────────────────────────────────

class VaultRetriever:
    """
    Semantic search over all ChromaDB vault collections.

    Satisfies the core.prompt_evaluator.Retriever protocol:
        retrieve(query, top_k) -> list[tuple[str, float]]

    On first use, bootstraps empty vault collections from disk using
    reindex_all_buckets(skip_if_indexed=True).  Skips buckets that are
    already populated so the bootstrap is idempotent.

    Results are formatted as "[vault:{bucket}/{content}]\\n{body}" so the
    agent can trace which vault doc the context came from.  Total injected
    text is capped by VAULT_TOKEN_BUDGET.
    """

    def __init__(self, min_score: float = 0.0) -> None:
        self._min_score    = min_score
        self._bootstrapped = False

    def _ensure_bootstrapped(self) -> None:
        if self._bootstrapped:
            return
        self._bootstrapped = True
        try:
            from memory.vault import reindex_all_buckets
            from core.log import log
            result = reindex_all_buckets(skip_if_indexed=True)
            log.info(result, source="rag")
        except Exception as e:
            from core.log import log
            log.warning(f"vault bootstrap skipped: {e}", source="rag")

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """
        Embed the query and return the most relevant vault docs within
        VAULT_TOKEN_BUDGET.

        Args:
            query:  Natural language query string.
            top_k:  Candidates to request per bucket from ChromaDB.

        Returns:
            List of (formatted_content, score) tuples, sorted by score
            descending.  Content is prefixed with [vault:bucket/doc].
        """
        self._ensure_bootstrapped()

        try:
            from memory.vault import query_all
        except Exception:
            return []

        try:
            hits = query_all(query, top_k=top_k)
        except Exception:
            return []

        output: list[tuple[str, float]] = []
        token_count = 0

        for bucket, content_name, body, score in hits:
            if score < self._min_score:
                continue
            formatted = f"[vault:{bucket}/{content_name}]\n{body}"
            doc_tokens = _count_tokens(formatted)
            if token_count + doc_tokens > VAULT_TOKEN_BUDGET:
                break
            token_count += doc_tokens
            output.append((formatted, round(score, 4)))

        return output


# ── Bootstrap helper ──────────────────────────────────────────────────────────

def _parse_frontmatter_field(content: str, field: str) -> str:
    """Extract a single YAML frontmatter field value."""
    stripped = content.strip()
    if not stripped.startswith("---"):
        return ""
    end = stripped.find("\n---", 3)
    if end == -1:
        return ""
    frontmatter = stripped[3:end]
    for line in frontmatter.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == field:
            return value.strip()
    return ""


def _index_skills_dir(skills_dir: str) -> None:
    """
    Bootstrap: embed all .md files in skills_dir into the skills collection.

    Called once on first use when the collection is empty.
    """
    from core.log import log

    skills_path = Path(skills_dir)
    if not skills_path.exists():
        return

    count = 0
    for md_file in sorted(skills_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
        except Exception:
            continue

        name        = _parse_frontmatter_field(content, "name") or md_file.stem
        description = _parse_frontmatter_field(content, "description") or name

        try:
            embed_skill(name=name, description=description, content=content)
            count += 1
        except Exception as e:
            log.error(f"skill bootstrap failed for {md_file.name}: {e}", source="rag")

    log.info(f"skill bootstrap complete — {count} skills indexed", source="rag")
