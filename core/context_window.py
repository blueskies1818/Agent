"""
core/context_window.py — Scored page stack for context management.

Pages are pushed onto the stack from any source (system, agent, memory,
skill, user). Each page carries a relevance score set at creation time
and a recency score that decays each turn.

    final_score = relevance × RELEVANCE_WEIGHT + recency × RECENCY_WEIGHT

When a new page would push total token usage over MAX_CONTEXT_TOKENS,
the page with the lowest final_score is evicted — not necessarily the
oldest one. This keeps high-relevance older pages alive while stale
low-relevance pages get dropped first.

Page sources
────────────
  system   Auto-injected each turn (sandbox state, turn counter).
  agent    Results from shell commands / actions the AI ran.
  memory   Facts retrieved from persistent memory / RAG.
  skill    Full skill definitions loaded on demand.
  user     Relevant snippets surfaced from past user messages.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Literal

Source = Literal["system", "agent", "memory", "skill", "user"]


# ── Page ──────────────────────────────────────────────────────────────────────

@dataclass
class Page:
    content: str
    source: Source
    relevance_score: float    # 0.0–1.0, fixed at creation
    turn_added: int
    tokens: int               # approximate token count

    def __repr__(self) -> str:
        return f"<Page source={self.source!r} rel={self.relevance_score:.2f} tokens={self.tokens}>"


def _count_tokens(text: str) -> int:
    """Rough approximation: 1 token ≈ 4 characters."""
    return max(1, len(text) // 4)


# ── Context window ─────────────────────────────────────────────────────────────

class ContextWindow:
    """
    Scored page stack with automatic eviction.

    Usage:
        ctx = ContextWindow(max_tokens=8000)

        # Each agentic turn:
        ctx.tick()
        ctx.push("sandbox is at /workspace", source="system", relevance_score=1.0)

        # Get formatted context for system prompt:
        system_prompt = f"...{ctx.render()}..."

        # Inspect:
        used, total = ctx.token_usage
    """

    def __init__(
        self,
        max_tokens: int,
        relevance_weight: float = 0.6,
        recency_weight: float = 0.4,
        on_evict: Callable[[Page], None] | None = None,
    ) -> None:
        self._max_tokens        = max_tokens
        self._rel_w             = relevance_weight
        self._rec_w             = recency_weight
        self._pages: list[Page] = []
        self._turn              = 0
        self._on_evict          = on_evict

    # ── Public API ─────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """Advance the internal turn counter. Call once per agentic loop iteration."""
        self._turn += 1

    def push(
        self,
        content: str,
        source: Source,
        relevance_score: float = 1.0,
    ) -> None:
        """
        Add a page to the stack.

        If adding it would exceed max_tokens, the lowest-scored page is
        evicted first (repeat until it fits or only one page remains).
        Credential values are scrubbed before storage as a safety net.
        """
        if not content or not content.strip():
            return

        try:
            from mods.passwd.cache import scrub
            content = scrub(content)
        except Exception:
            pass

        page = Page(
            content=content.strip(),
            source=source,
            relevance_score=max(0.0, min(1.0, relevance_score)),
            turn_added=self._turn,
            tokens=_count_tokens(content),
        )
        self._pages.append(page)
        self._evict_if_needed()

    def score(self, page: Page) -> float:
        """
        Compute the current score for a page.

        Recency decays by 20% per turn of age. A page added this turn
        has recency=1.0; one added 5 turns ago has recency≈0.50.
        """
        age = self._turn - page.turn_added
        recency = 1.0 / (1.0 + age * 0.2)
        return page.relevance_score * self._rel_w + recency * self._rec_w

    def render(self) -> str:
        """
        Return all pages formatted for injection into a system prompt.

        Pages are ordered from lowest to highest score so the most
        relevant content sits closest to the end of the prompt (where
        LLMs attend most strongly).
        """
        if not self._pages:
            return "(context window is empty)"

        sorted_pages = sorted(self._pages, key=self.score)
        parts = []
        for page in sorted_pages:
            s = self.score(page)
            header = f"[{page.source.upper()} | score {s:.2f}]"
            parts.append(f"{header}\n{page.content}")

        return "\n\n---\n\n".join(parts)

    def clear_source(self, source: Source) -> None:
        """Remove all pages from a specific source (e.g. stale system pages)."""
        self._pages = [p for p in self._pages if p.source != source]

    def clear(self) -> None:
        """Remove all pages. Used by the worker context — reset each node invocation."""
        self._pages = []

    # ── Properties ─────────────────────────────────────────────────────────────

    @property
    def page_count(self) -> int:
        return len(self._pages)

    @property
    def token_usage(self) -> tuple[int, int]:
        """Returns (used_tokens, max_tokens)."""
        return self._total_tokens(), self._max_tokens

    @property
    def current_turn(self) -> int:
        return self._turn

    # ── Internals ──────────────────────────────────────────────────────────────

    def _total_tokens(self) -> int:
        return sum(p.tokens for p in self._pages)

    def _evict_if_needed(self) -> None:
        """Evict lowest-scored pages until total tokens fit within the budget."""
        while self._total_tokens() > self._max_tokens and len(self._pages) > 1:
            weakest = min(self._pages, key=self.score)
            if self._on_evict is not None:
                self._on_evict(weakest)
            self._pages.remove(weakest)

    def __repr__(self) -> str:
        used, total = self.token_usage
        return f"<ContextWindow pages={self.page_count} tokens={used}/{total} turn={self._turn}>"