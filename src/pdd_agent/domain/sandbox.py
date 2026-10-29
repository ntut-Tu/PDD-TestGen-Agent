"""Sandbox runs: the shape of the sandbox-controller's SandboxRun resource (ADR 0003)."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from pdd_agent.domain.jobs import CamelModel

Phase = Literal["Pending", "Preparing", "Running", "Succeeded", "Failed", "Error"]
TERMINAL_PHASES: frozenset[str] = frozenset({"Succeeded", "Failed", "Error"})

# Reasons of an ``Error`` run, which are also reasons of the job's sandbox result.
ERROR_REASONS: frozenset[str] = frozenset(
    {"sandbox_unavailable", "unsupported_project", "source_unavailable", "internal"}
)


class SandboxFile(CamelModel):
    """A file written into the run's workspace."""

    path: str
    content: str


class SandboxSpec(CamelModel):
    """What to run: the pull request head plus the test files to add on top of it."""

    account: str
    project_id: int
    repo_id: int
    head_sha: str
    files: list[SandboxFile]
    test_classes: list[str]


class ClassResult(CamelModel):
    """Outcome counts of one test class."""

    class_name: str = Field(alias="class")
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0


class SandboxResult(CamelModel):
    """Build and test outcome of a finished run."""

    compiled: bool = False
    tests: list[ClassResult] = Field(default_factory=list)
    output: str = ""


class SandboxStatus(CamelModel):
    """Observed state of a run."""

    phase: Phase
    reason: str | None = None
    message: str = ""
    build_tool: str | None = None
    result: SandboxResult | None = None


class SandboxRun(CamelModel):
    """One try: an immutable spec and the status the controller reports for it."""

    id: str
    spec: SandboxSpec
    status: SandboxStatus

    @property
    def finished(self) -> bool:
        """Whether the run reached a terminal phase."""
        return self.status.phase in TERMINAL_PHASES


def summarize(status: SandboxStatus) -> str:
    """One-line description of a run's outcome for try records."""
    result = status.result
    if result is None or not result.tests:
        return status.message
    totals = (
        sum(t.passed for t in result.tests),
        sum(t.failed + t.errors for t in result.tests),
    )
    counts = f"{totals[0]} passed, {totals[1]} failed or errored"
    return f"{status.message} ({counts})" if status.message else counts
