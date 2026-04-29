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

    def call(self, messages: list[dict], system: str) -> str:
        """Single-shot completion. Returns the full assistant reply as a string."""
        return self._raw_call(messages, system)

    def stream(self, messages: list[dict], system: str) -> Iterator[str]:
        """Streaming completion. Yields text chunks as they arrive."""
        yield from self._raw_stream(messages, system)

    # ── Provider interface (implement in subclasses) ───────────────────────────

    @abstractmethod
    def _raw_call(self, messages: list[dict], system: str) -> str:
        """Send messages to the API and return the complete reply text."""

    @abstractmethod
    def _raw_stream(self, messages: list[dict], system: str) -> Iterator[str]:
        """Send messages to the API and yield reply text in chunks."""
