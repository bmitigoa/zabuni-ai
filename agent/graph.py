"""The LangGraph StateGraph: Planner -> Investigator (loops) -> Synthesiser -> human_review (Day 4 placeholder) -> END.

  START -> planner -> investigator --(steps remain)--> investigator
                          |
                          +--(queue empty or step cap)--> synthesiser -> human_review -> END

Why this is an agent and not a pipeline: the *sequence* of tool calls is not fixed in code. The Planner chooses checks
and arguments from the discovered tool catalogue; the Investigator decides after every result whether to add follow-up
checks, widen a search that returned too few comparables, or repair a failing call; the Synthesiser decides what the
evidence supports. The graph's edge after the Investigator is conditional on state that the run itself produced.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph

from agent.nodes import (AgentContext, human_review, investigator, planner, route_after_investigator, synthesiser)
from agent.state import AgentState

RUN_ID_RANDOM_CHARS = 4
RECURSION_PADDING = 10        # graph visits beyond the step cap that are still allowed (planner, synthesiser, ...)


def new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:RUN_ID_RANDOM_CHARS]}"


def build_graph(ctx: AgentContext):
    """Compile the graph with the run's dependencies bound into each node."""
    g = StateGraph(AgentState)
    g.add_node("planner", partial(planner, ctx=ctx))
    g.add_node("investigator", partial(investigator, ctx=ctx))
    g.add_node("synthesiser", partial(synthesiser, ctx=ctx))
    g.add_node("human_review", partial(human_review, ctx=ctx))
    g.add_edge(START, "planner")
    g.add_edge("planner", "investigator")
    g.add_conditional_edges("investigator", route_after_investigator,
                            {"investigator": "investigator", "synthesiser": "synthesiser"})
    g.add_edge("synthesiser", "human_review")
    g.add_edge("human_review", END)
    return g.compile()


async def run_agent(ctx: AgentContext, query: str) -> dict[str, Any]:
    """Run the graph for one request and return the final state."""
    graph = build_graph(ctx)
    config = {"recursion_limit": int(ctx.cfg["max_steps"]) + int(ctx.cfg["max_plan_steps"]) + RECURSION_PADDING}
    return await graph.ainvoke({"run_id": ctx.audit.run_id, "query": query}, config=config)
