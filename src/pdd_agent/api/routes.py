"""HTTP routes of the test-agent."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from pdd_agent.api.dependencies import Caller, Deps
from pdd_agent.api.schemas import GenerationRequest, JobEnvelope, JobList
from pdd_agent.domain.quota import Quota

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@router.post(
    "/api/v1/projects/{project_id}/repos/{repo_id}/pulls/{number}/generations",
    status_code=202,
    response_model=JobEnvelope,
    response_model_by_alias=True,
)
async def create_generation(
    project_id: int,
    repo_id: int,
    number: int,
    body: GenerationRequest,
    caller: Caller,
    deps: Deps,
) -> JobEnvelope:
    """Create a test generation job for a pull request."""
    services = deps.services
    job = await services.jobs.create(
        caller, project_id, repo_id, number, body.production_paths, body.test_paths
    )
    if job.status == "queued":
        services.runner.submit(job.id)
    return JobEnvelope(job=job)


@router.get(
    "/api/v1/projects/{project_id}/repos/{repo_id}/pulls/{number}/generations",
    response_model=JobList,
    response_model_by_alias=True,
)
async def list_generations(
    project_id: int,
    repo_id: int,
    number: int,
    caller: Caller,
    deps: Deps,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> JobList:
    """List the newest jobs of a pull request."""
    jobs = await deps.services.jobs.list_for_pull(caller, project_id, repo_id, number, limit)
    return JobList(jobs=jobs)


@router.get(
    "/api/v1/generations/{job_id}",
    response_model=JobEnvelope,
    response_model_by_alias=True,
)
async def get_generation(job_id: str, caller: Caller, deps: Deps) -> JobEnvelope:
    """Return one job; only its creator or a member of its project may read it."""
    return JobEnvelope(job=await deps.services.jobs.get(caller, job_id))


@router.get("/api/v1/quota", response_model=Quota, response_model_by_alias=True)
async def get_quota(_: Caller, deps: Deps) -> Quota:
    """Return today's language model request allowance."""
    return deps.services.jobs.quota()
