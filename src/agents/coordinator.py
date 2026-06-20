"""LangGraph coordinator for the OpenRabbit multi-agent review system.

The coordinator builds a :class:`langgraph.graph.StateGraph` that:

1. Accepts the initial :class:`~agents.models.ReviewState`.
2. Fans out to every registered review agent in parallel.
3. Merges their :class:`~agents.models.AgentResult` objects into the state.

Each agent runs in its own node. If an agent raises an exception its
contribution is an empty :class:`~agents.models.AgentResult` so the review
still completes.

Usage::

    from agents.coordinator import CoordinatorGraph

    graph = CoordinatorGraph(agents=[SecurityAgent(), PerformanceAgent()])
    app = graph.compile()
    result = await app.ainvoke(initial_state)
    all_results = result["agent_results"]
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.graph import END, StateGraph

from agents.base import BaseReviewAgent
from agents.models import AgentResult, ReviewState

logger = logging.getLogger(__name__)

_MERGE_NODE = "merge"


class CoordinatorGraph:
    """Builds and compiles the LangGraph review graph.

    Parameters
    ----------
    agents:
        List of :class:`~agents.base.BaseReviewAgent` instances to run in
        parallel. Each agent becomes its own LangGraph node.
    """

    def __init__(self, agents: list[BaseReviewAgent]) -> None:
        self._agents = agents

    def compile(self) -> Any:
        """Build and return a compiled LangGraph application."""
        builder = StateGraph(ReviewState)

        agent_node_names: list[str] = []
        for agent in self._agents:
            node_name = f"agent_{agent.name}"
            agent_node_names.append(node_name)
            builder.add_node(node_name, _make_agent_node(agent))

        builder.add_node(_MERGE_NODE, _merge_node)

        if agent_node_names:
            # Fan out from START to each agent node.
            builder.set_entry_point(agent_node_names[0])
            for i, name in enumerate(agent_node_names[:-1]):
                builder.add_edge(name, agent_node_names[i + 1])
            builder.add_edge(agent_node_names[-1], _MERGE_NODE)
        else:
            builder.set_entry_point(_MERGE_NODE)

        builder.add_edge(_MERGE_NODE, END)

        return builder.compile()


# ---------------------------------------------------------------------------
# Node factories
# ---------------------------------------------------------------------------


def _make_agent_node(agent: BaseReviewAgent) -> Any:
    """Return a LangGraph node function for *agent*.

    Each node appends one :class:`~agents.models.AgentResult` to
    ``agent_results`` in the shared state.
    """

    async def node(state: ReviewState) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = await agent.run(state)
        except Exception:
            logger.exception("Agent %r raised an unexpected error", agent.name)
            result = AgentResult(
                agent=agent.name,
                findings=[],
                confidence=0.0,
                execution_time=time.monotonic() - started,
            )
        existing: list[AgentResult] = list(state.get("agent_results") or [])
        return {"agent_results": [*existing, result]}

    node.__name__ = f"agent_{agent.name}"
    return node


def _merge_node(state: ReviewState) -> dict[str, Any]:
    """Pass-through: results were already collected by agent nodes."""
    return {"agent_results": list(state.get("agent_results") or [])}
