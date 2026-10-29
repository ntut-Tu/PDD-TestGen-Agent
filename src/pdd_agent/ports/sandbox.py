"""Port of the sandbox that compiles and runs generated tests."""

from __future__ import annotations

from typing import Protocol

from pdd_agent.domain.sandbox import SandboxRun, SandboxSpec


class SandboxRunner(Protocol):
    """Creates sandbox runs and reads their status.

    Every method raises ``SandboxError`` when the sandbox cannot serve the request.
    """

    async def submit(self, spec: SandboxSpec) -> SandboxRun:
        """Create a run for ``spec``; it starts out ``Pending``."""
        ...

    async def get(self, run_id: str) -> SandboxRun:
        """Return the current state of a run."""
        ...

    async def delete(self, run_id: str) -> None:
        """Ask for a run to be removed; removal completes asynchronously."""
        ...
