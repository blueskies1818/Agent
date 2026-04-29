"""
core/prompt_evaluator.py — Proactive context retrieval from user prompts.

Before the agent runs, this evaluates each user message and surfaces
relevant pages to pre-load into the context window.

V2 changes:
  - Skill matching is now semantic (SkillRetriever) instead of keyword-based
  - SkillRetriever returns hints (name + description only) capped by SKILL_TOKEN_BUDGET
  - core_ref.md is never injected here — it's always-injected by the loop
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


# ── RAG retriever protocol ─────────────────────────────────────────────────────

@runtime_checkable
class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        ...


# ── Skill retriever protocol ───────────────────────────────────────────────────

@runtime_checkable
class SkillHintRetriever(Protocol):
    def retrieve_hints(self, query: str, top_k: int = 5) -> list[tuple[str, str, float]]:
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
        rag: Retriever | None = None,
        skill_rag: SkillHintRetriever | None = None,
        vault_rag: Retriever | None = None,
        min_score: float = 0.4,
        rag_top_k: int = 5,
        skill_top_k: int = 3,
        vault_top_k: int = 5,
        # Legacy keyword-based skills_dir is no longer used — kept for
        # call-site compatibility during the transition; ignored.
        skills_dir: object = None,
    ) -> None:
        self._rag         = rag
        self._skill_rag   = skill_rag
        self._vault_rag   = vault_rag
        self._min_score   = min_score
        self._rag_top_k   = rag_top_k
        self._skill_top_k = skill_top_k
        self._vault_top_k = vault_top_k

    def evaluate(self, user_input: str) -> list[RetrievedPage]:
        pages: list[RetrievedPage] = []

        # Memory retrieval
        if self._rag is not None:
            for content, score in self._rag.retrieve(user_input, top_k=self._rag_top_k):
                if score >= self._min_score:
                    pages.append(RetrievedPage(
                        content=content,
                        source="memory",
                        relevance_score=score,
                    ))

        # Skill hints — name + description only, never full content
        if self._skill_rag is not None:
            for name, desc, score in self._skill_rag.retrieve_hints(
                user_input, top_k=self._skill_top_k
            ):
                if score >= self._min_score:
                    pages.append(RetrievedPage(
                        content=(
                            f"Skill available: '{name}' — {desc}\n"
                            f"Request it with: <action type=\"skill\"><n>{name}</n></action>"
                        ),
                        source="skill",
                        relevance_score=score,
                    ))

        # Vault knowledge — full doc bodies, token-budgeted
        if self._vault_rag is not None:
            for content, score in self._vault_rag.retrieve(
                user_input, top_k=self._vault_top_k
            ):
                if score >= self._min_score:
                    pages.append(RetrievedPage(
                        content=content,
                        source="vault",
                        relevance_score=score,
                    ))

        pages.sort(key=lambda p: p.relevance_score, reverse=True)
        return pages
