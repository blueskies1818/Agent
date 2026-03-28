"""
engine/graph.py — Assembles and compiles the LangGraph state machine.

Graph shape
───────────
          ┌─────────┐
  START → │ planner │
          └────┬────┘
               │
          ┌────▼────┐
     ┌──→ │  actor  │
     │    └────┬────┘
     │         │
     │    ┌────▼──────┐
     │    │ reflector │
     │    └────┬──────┘
     │         │
     │   done? │ turn limit?
     │    no ──┘           yes
     └───────────────────→ END

Usage
─────
    from engine.graph import build_graph
    graph = build_graph(agent)
    graph.invoke(initial_state)
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, StateGraph

from agents.base import BaseAgent
from engine.nodes import actor, planner, reflector, should_continue
from engine.state import AgentState


def build_graph(agent: BaseAgent):
    """
    Build and compile the agent graph for one session.

    The agent and loaded_skills set are closed over so the graph
    nodes are pure functions with no global state.
    """
    loaded_skills: set[str] = set()

    # Bind agent + shared mutable skill cache to each node
    _planner   = partial(planner, agent=agent)
    _actor     = partial(actor,   agent=agent, loaded_skills=loaded_skills)
    _reflector = reflector

    graph = StateGraph(AgentState)

    graph.add_node("planner",   _planner)
    graph.add_node("actor",     _actor)
    graph.add_node("reflector", _reflector)

    # Edges
    graph.set_entry_point("planner")
    graph.add_edge("planner",   "actor")
    graph.add_edge("actor",     "reflector")

    # Conditional: reflector decides actor (loop) or END
    graph.add_conditional_edges(
        "reflector",
        should_continue,
        {
            "actor": "actor",
            "end":   END,
        },
    )

    return graph.compile()