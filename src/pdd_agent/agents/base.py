"""The interface every agent implements."""

from __future__ import annotations

from typing import Protocol


class Agent[TaskT, OutputT](Protocol):
    """Turns one task into one output.

    An agent reaches the outside world only through ports (``pdd_agent.ports``) handed to
    its constructor, so it runs the same under a web service, a worker or a unit test.
    """

    async def run(self, task: TaskT) -> OutputT:
        """Execute ``task``; failures are raised as ``pdd_agent.domain.errors.DomainError``."""
        ...
