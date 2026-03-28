"""
OpenAI provider — API glue only.

All parsing, retries, and error handling live in BaseAgent.
This file just sends messages to the OpenAI API and returns raw text.
"""

import os
from collections.abc import Iterator

from dotenv import load_dotenv
from openai import OpenAI

from agents.base import BaseAgent
from config import PROVIDERS

load_dotenv()

_MODELS = PROVIDERS["openai"]["models"]


class OpenAIAgent(BaseAgent):

    def __init__(self) -> None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError("OPENAI_API_KEY is not set.")
        self._client = OpenAI(api_key=api_key)

    def _raw_call(self, messages, system, tier) -> str:
        response = self._client.chat.completions.create(
            model=_MODELS[tier],
            messages=[{"role": "system", "content": system}] + messages,
            max_completion_tokens=4096,
        )
        return response.choices[0].message.content

    def _raw_stream(self, messages, system, tier) -> Iterator[str]:
        stream = self._client.chat.completions.create(
            model=_MODELS[tier],
            messages=[{"role": "system", "content": system}] + messages,
            max_completion_tokens=4096,
            stream=True,
        )
        for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content