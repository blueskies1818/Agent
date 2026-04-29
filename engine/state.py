"""
engine/state.py — Shared state that flows through the LangGraph.

Every node reads from and returns a partial update to AgentState.
LangGraph merges the partial updates using the reducer functions
defined in the Annotated type hints.

We intentionally avoid importing from langchain_core to prevent
Pydantic V1 compatibility warnings on Python 3.14+.
"""

from __future__ import annotations

from typing import Annotated, TypedDict


def _add_messages(left: list[dict], right: list[dict]) -> list[dict]:
    """Append new messages to the existing list."""
    return left + right


class AgentState(TypedDict):
    messages:     Annotated[list[dict], _add_messages]
    plan:         list[str]
    plan_step:    int           # current step index in the plan file
    actor_turn:   int
    done:         bool
    blocked:      bool          # actor signalled escalation
    escalation:   dict | None   # {level, reason, need} from escalate action
    system:       str
    last_actions: list[str]