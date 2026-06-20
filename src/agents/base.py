"""Abstract base class for all OpenRabbit review agents."""

from __future__ import annotations

import abc

from agents.models import AgentResult, ReviewState


class BaseReviewAgent(abc.ABC):
    """Contract every review agent must fulfill.

    Subclasses set a class-level ``name`` attribute and implement
    :meth:`run`. The coordinator calls :meth:`run` and catches any exception
    so that one failing agent does not abort the whole review.
    """

    name: str

    @abc.abstractmethod
    async def run(self, state: ReviewState) -> AgentResult:
        """Execute the review and return a result.

        Parameters
        ----------
        state:
            The shared LangGraph state. Agents may read ``pr_payload`` and
            ``retrieval_result`` but must not write to the state directly --
            returning an :class:`~agents.models.AgentResult` is the only
            allowed side-effect.

        Returns
        -------
        AgentResult
            An :class:`~agents.models.AgentResult` with zero or more
            :class:`~agents.models.Finding` objects. Never raise; the
            coordinator wraps this call in a try/except.
        """
