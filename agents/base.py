"""
BaseAgent — the contract every provider must fulfil.

Providers only implement two private methods (_raw_call / _raw_stream).
Everything else (message formatting, public API) lives here so it stays
consistent across providers.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator


class BaseAgent(ABC):
    """Abstract LLM provider. Subclass and implement _raw_call + _raw_stream."""

    # ── Public API (used by the reactive loop) ────────────────────────────────

    def call(self, messages: list[dict], system: str, tier: str) -> str:
        """
        Single-shot completion. Returns the full assistant reply as a string.

        Args:
            messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
            system:   System prompt string.
            tier:     "fast" or "smart" — maps to a model in config.PROVIDERS.
        """
        return self._raw_call(messages, system, tier)

    def stream(self, messages: list[dict], system: str, tier: str) -> Iterator[str]:
        """
        Streaming completion. Yields text chunks as they arrive.
        Caller is responsible for joining chunks into a full response.
        """
        yield from self._raw_stream(messages, system, tier)

    # ── Provider interface (implement in subclasses) ───────────────────────────

    @abstractmethod
    def _raw_call(self, messages: list[dict], system: str, tier: str) -> str:
        """Send messages to the API and return the complete reply text."""

    @abstractmethod
    def _raw_stream(self, messages: list[dict], system: str, tier: str) -> Iterator[str]:
        """Send messages to the API and yield reply text in chunks."""
