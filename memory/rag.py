"""
memory/rag.py — Semantic retriever over ChromaDB.

Implements the core.prompt_evaluator.Retriever protocol so it can be
passed directly into PromptEvaluator without any other changes.

Usage:
    from memory.rag import MemoryRetriever

    retriever = MemoryRetriever()
    results = retriever.retrieve("how do I write Python files?", top_k=5)
    for content, score in results:
        print(f"{score:.2f}  {content[:80]}")
"""

from __future__ import annotations

from memory.embedder import _embed, _get_collection


class MemoryRetriever:
    """
    Semantic search over the ChromaDB memory collection.

    Satisfies the core.prompt_evaluator.Retriever protocol:
        retrieve(query, top_k) -> list[tuple[str, float]]

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

    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """
        Embed the query and return the top-k most similar stored facts.

        Returns:
            List of (content, score) tuples, sorted by score descending.
            Empty list if the collection has no entries or nothing is
            above min_score.
        """
        col = _get_collection()
        if col.count() == 0:
            return []

        # Cap top_k at what's actually stored
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
        for doc, dist in zip(docs, distances):
            score = max(0.0, 1.0 - dist)         # convert distance → similarity
            if score >= self._min_score:
                output.append((doc, round(score, 4)))

        # Already sorted by distance ascending = score descending
        return output

    def retrieve_with_metadata(
        self, query: str, top_k: int = 5
    ) -> list[dict]:
        """
        Like retrieve() but also returns metadata for each result.

        Returns:
            List of dicts with keys: content, score, metadata.
        """
        col = _get_collection()
        if col.count() == 0:
            return []

        n         = min(top_k, col.count())
        embedding = _embed(query)
        results   = col.query(
            query_embeddings=[embedding],
            n_results=n,
            include=["documents", "distances", "metadatas"],
        )

        docs      = results["documents"][0]
        distances = results["distances"][0]
        metas     = results["metadatas"][0]

        output = []
        for doc, dist, meta in zip(docs, distances, metas):
            score = max(0.0, 1.0 - dist)
            if score >= self._min_score:
                output.append({
                    "content":  doc,
                    "score":    round(score, 4),
                    "metadata": meta,
                })

        return output