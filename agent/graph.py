"""The LangGraph StateGraph with the human approval gate.

  START -> planner -> investigator --(steps remain)--> investigator
                          |
                          +--(queue empty / budget)--> synthesiser -> human_review  (INTERRUPT: the run pauses here)
                                                                         |  ^
                                                    decisions applied -> |  | still undecided
                                                          +--------------+--+
                                       clarify requested -> clarify -----+   all decided -> memo -> END

Why this is an agent and not a pipeline: the *sequence* of tool calls is not fixed in code. The Planner chooses checks
and arguments from the discovered tool catalogue; the Investigator decides after every result whether to add follow-up
checks, widen a search that returned too few comparables, or repair a failing call; the Synthesiser decides what the
evidence supports; a reviewer's question sends the run back to investigate that flag again.

The pause: `human_review` calls LangGraph's `interrupt()`. With a checkpointer (SQLite, one file) the whole state is saved
under thread_id = run_id, the process may exit, and `python -m agent.review <run_id>` resumes the same run later.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, AsyncIterator

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from agent.nodes import (AgentContext, clarify, human_review, investigator, memo, planner, route_after_investigator,
                         route_after_review, synthesiser)
from agent.state import AgentState

RUN_ID_RANDOM_CHARS = 4
RECURSION_PADDING = 10        # graph visits beyond the step cap that are still allowed (planner, synthesiser, ...)


def new_run_id() -> str:
    return f"run_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:RUN_ID_RANDOM_CHARS]}"


def build_graph(ctx: AgentContext, checkpointer: Any = None):
    """Compile the graph with the run's dependencies bound into each node."""
    g = StateGraph(AgentState)
    for name, fn in (("planner", planner), ("investigator", investigator), ("synthesiser", synthesiser),
                     ("human_review", human_review), ("clarify", clarify), ("memo", memo)):
        g.add_node(name, partial(fn, ctx=ctx))
    g.add_edge(START, "planner")
    g.add_edge("planner", "investigator")
    g.add_conditional_edges("investigator", route_after_investigator,
                            {"investigator": "investigator", "synthesiser": "synthesiser"})
    g.add_edge("synthesiser", "human_review")
    g.add_conditional_edges("human_review", route_after_review,
                            {"clarify": "clarify", "human_review": "human_review", "memo": "memo"})
    g.add_edge("clarify", "human_review")
    g.add_edge("memo", END)
    return g.compile(checkpointer=checkpointer)


@asynccontextmanager
async def sqlite_checkpointer(path: Path) -> AsyncIterator[AsyncSqliteSaver]:
    """The persistent checkpointer (a single SQLite file) that lets a paused run survive a process exit."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        yield saver


def _config(ctx: AgentContext) -> dict[str, Any]:
    limit = int(ctx.cfg["max_steps"]) + int(ctx.cfg["max_plan_steps"]) + RECURSION_PADDING + int(ctx.cfg["review_round_allowance"])
    return {"configurable": {"thread_id": ctx.audit.run_id}, "recursion_limit": limit}


def pause_payload(result: dict[str, Any]) -> dict[str, Any] | None:
    """The review request a paused run is waiting on (the value passed to `interrupt`), or None if the run finished."""
    pending = result.get("__interrupt__")
    return pending[0].value if pending else None


async def run_agent(ctx: AgentContext, query: str, checkpointer: Any = None) -> dict[str, Any]:
    """Run the graph for one request. Returns the state, plus `__interrupt__` when it paused for review."""
    graph = build_graph(ctx, checkpointer)
    return await graph.ainvoke({"run_id": ctx.audit.run_id, "query": query}, config=_config(ctx))


async def resume_agent(ctx: AgentContext, decisions: list[dict[str, Any]], checkpointer: Any) -> dict[str, Any]:
    """Resume a paused run (thread_id = ctx.audit.run_id) with the reviewer's decisions."""
    graph = build_graph(ctx, checkpointer)
    return await graph.ainvoke(Command(resume={"decisions": decisions}), config=_config(ctx))


async def saved_state(ctx: AgentContext, checkpointer: Any) -> dict[str, Any] | None:
    """The checkpointed state of a run (None if the run is unknown)."""
    snap = await build_graph(ctx, checkpointer).aget_state(_config(ctx))
    return dict(snap.values) if snap and snap.values else None


async def pending_payload(ctx: AgentContext, checkpointer: Any) -> dict[str, Any] | None:
    """The review request a checkpointed run is paused on (None when it is not paused)."""
    snap = await build_graph(ctx, checkpointer).aget_state(_config(ctx))
    for task in getattr(snap, "tasks", None) or ():
        for it in getattr(task, "interrupts", None) or ():
            return it.value
    return None
