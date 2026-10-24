"""Request and response bodies of the HTTP API (camelCase on the wire)."""

from __future__ import annotations

from pydantic import Field

from pdd_agent.domain.jobs import CamelModel, Job


class GenerationRequest(CamelModel):
    """Body of the job creation request."""

    production_paths: list[str] = Field(default_factory=list)
    test_paths: list[str] = Field(default_factory=list)


class JobEnvelope(CamelModel):
    """Response holding one job."""

    job: Job


class JobList(CamelModel):
    """Response holding several jobs, newest first."""

    jobs: list[Job]
