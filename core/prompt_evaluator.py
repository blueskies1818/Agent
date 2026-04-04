"""
core/prompt_evaluator.py — Proactive context retrieval from user prompts.

Before the agent runs, this evaluates each user message and surfaces
relevant pages to pre-load into the context window.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable


# ── RAG retriever protocol ─────────────────────────────────────────────────────

@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        ...


# ── Retrieved page ─────────────────────────────────────────────────────────────

@dataclass
class RetrievedPage:
    content: str
    source: str
    relevance_score: float


# ── Evaluator ──────────────────────────────────────────────────────────────────

class PromptEvaluator:
    def __init__(
        self,
        skills_dir: str | Path,
        rag: Retriever | None = None,
        min_score: float = 0.4,
        rag_top_k: int = 5,
    ) -> None:
        self._skills_dir = Path(skills_dir)
        self._rag        = rag
        self._min_score  = min_score
        self._rag_top_k  = rag_top_k

    def evaluate(self, user_input: str) -> list[RetrievedPage]:
        pages: list[RetrievedPage] = []

        if self._rag is not None:
            for content, score in self._rag.retrieve(user_input, top_k=self._rag_top_k):
                if score >= self._min_score:
                    pages.append(RetrievedPage(content=content, source="memory", relevance_score=score))

        pages.extend(self._match_skills(user_input))
        pages.sort(key=lambda p: p.relevance_score)
        return pages

    def _match_skills(self, text: str) -> list[RetrievedPage]:
        if not self._skills_dir.exists():
            return []

        text_lower = text.lower()
        pages: list[RetrievedPage] = []

        for md_file in sorted(self._skills_dir.glob("*.md")):
            name = md_file.stem.lower()

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Load keywords from frontmatter; fall back to skill name only
            keywords = _parse_frontmatter_keywords(content) or [name]

            if not any(kw in text_lower for kw in keywords):
                continue

            first_line = next(
                (ln.lstrip("#").strip() for ln in content.splitlines() if ln.startswith("#")),
                name,
            )

            pages.append(RetrievedPage(
                content=f"Skill available: '{name}' — {first_line}\n"
                        f"Request it with: <action type=\"skill\"><n>{name}</n></action>",
                source="skill",
                relevance_score=0.70,
            ))

        return pages


# ── Frontmatter keyword parser ────────────────────────────────────────────────

def _parse_frontmatter_keywords(content: str) -> list[str]:
    """
    Extract keywords from a skill file's YAML frontmatter block.

    Expects the file to start with --- and contain a keywords: field:
        ---
        keywords: ffmpeg, video, convert, encode
        ---

    Returns an empty list if no frontmatter or no keywords field is found,
    so the caller can fall back to the hardcoded _SKILL_KEYWORDS registry.
    """
    stripped = content.strip()
    if not stripped.startswith("---"):
        return []
    end = stripped.find("\n---", 3)
    if end == -1:
        return []
    frontmatter = stripped[3:end]
    for line in frontmatter.splitlines():
        if line.strip().startswith("keywords:"):
            value = line.split(":", 1)[1].strip()
            return [kw.strip().lower() for kw in value.split(",") if kw.strip()]
    return []
