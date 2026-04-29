"""
engine/graph.py — Assembles and compiles the LangGraph state machine.

V2 graph shape
──────────────
          ┌─────────┐
  START → │ planner │
          └────┬────┘
               │
          ┌────▼────┐
     ┌──→ │  actor  │ ←──┐
     │    └────┬────┘    │
     │         │         │
     │    ┌────▼──────┐  │
     │    │ reflector │  │
     │    └────┬──────┘  │
     │         │         │
     │    blocked?─────→ replanner ──┘
     │    done? yes ───→ END
     └─── no (continue loop)

Usage
─────
    from engine.graph import build_graph
    graph = build_graph(
        planner_agent, worker_agent,
        planner_ctx, worker_ctx,
        soul_planner, soul_worker, core_ref,
    )
    graph.invoke(initial_state)
"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, StateGraph

from providers.base import BaseAgent
from core.context_window import ContextWindow
from engine.nodes import actor, planner, reflector, replanner, should_continue
from engine.state import AgentState


def build_graph(
    planner_agent: BaseAgent,
    worker_agent:  BaseAgent,
    planner_ctx:   ContextWindow,
    worker_ctx:    ContextWindow,
    soul:          str,
    soul_planner:  str,
    soul_worker:   str,
    core_ref:      str,
):
    """
    Build and compile the agent graph for one session.

    Both agents plus their context windows and soul strings are closed over
    so graph nodes are pure functions with no global state.
    """
    loaded_skills: set[str] = set()

    # Actor (worker) gets soul + role instructions — it produces all user-visible output.
    # Planner and replanner get role instructions only — they plan, never speak to the user.
    combined_worker = f"{soul}\n\n---\n\n{soul_worker}" if soul_worker else soul

    _planner  = partial(
        planner,
        agent=planner_agent,
        ctx=planner_ctx,
        soul=soul_planner,
        core_ref=core_ref,
    )
    _actor    = partial(
        actor,
        agent=worker_agent,
        worker_ctx=worker_ctx,
        soul=combined_worker,
        core_ref=core_ref,
        loaded_skills=loaded_skills,
    )
    _replanner = partial(
        replanner,
        agent=planner_agent,
        ctx=planner_ctx,
        soul=soul_planner,
        core_ref=core_ref,
    )
    _reflector = reflector

    graph = StateGraph(AgentState)

    graph.add_node("planner",   _planner)
    graph.add_node("actor",     _actor)
    graph.add_node("reflector", _reflector)
    graph.add_node("replanner", _replanner)

    # Edges
    graph.set_entry_point("planner")
    graph.add_edge("planner",   "actor")
    graph.add_edge("actor",     "reflector")
    graph.add_edge("replanner", "actor")

    # Conditional: reflector decides next node
    graph.add_conditional_edges(
        "reflector",
        should_continue,
        {
            "actor":      "actor",
            "replanner":  "replanner",
            "end":        END,
        },
    )

    return graph.compile()
