"""
Anthropic Claude provider — API glue only.

All parsing, retries, and loop logic live in the reactive layer.
This file just sends messages to the Anthropic API and returns raw text.
"""

import os
from collections.abc import Iterator

import anthropic
from dotenv import load_dotenv

from providers.base import BaseAgent
from config import PROVIDERS

load_dotenv()

_MODEL = PROVIDERS["claude"]["model"]


class ClaudeAgent(BaseAgent):

    def __init__(self) -> None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
        self._client = anthropic.Anthropic(api_key=api_key)

    def _raw_call(self, messages: list[dict], system: str) -> str:
        response = self._client.messages.create(
            model=_MODEL,
            max_tokens=4096,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    def _raw_stream(self, messages: list[dict], system: str) -> Iterator[str]:
        with self._client.messages.stream(
            model=_MODEL,
            max_tokens=4096,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
