"""Job use cases: create, list and read jobs for authenticated callers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime

from pdd_agent.agents.testgen import GenerationAgent, GenerationTask
from pdd_agent.config import Settings
from pdd_agent.domain.errors import NotFoundError, SourceError, UpstreamError
from pdd_agent.domain.jobs import Job, JobContext, Principal, PullRef
from pdd_agent.domain.quota import Quota
from pdd_agent.domain.timeutil import to_iso, utc_now
from pdd_agent.integrations.pdd_backend import Membership
from pdd_agent.llm.usage import RateLimiter
from pdd_agent.service.reuse import reuse_key, reused
from pdd_agent.storage import JobRepository


class JobService:
    """Creates generation jobs and serves them to members of their project."""

    def __init__(
        self,
        settings: Settings,
        store: JobRepository,
        agent: GenerationAgent,
        membership: Membership,
        limiter: RateLimiter,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._settings = settings
        self._store = store
        self._agent = agent
        self._membership = membership
        self._limiter = limiter
        self._clock = clock

    async def create(
        self,
        principal: Principal,
        project_id: int,
        repo_id: int,
        number: int,
        production_paths: list[str],
        test_paths: list[str],
    ) -> Job:
        """Validate the request against the pull request and persist a job.

        The job is ``queued`` unless an identical recent request already has a result,
        in which case it is ``succeeded`` with that result and costs no model request.
        """
        await self._membership.ensure_member(principal, project_id)
        task = GenerationTask(
            principal.account, project_id, repo_id, number, production_paths, test_paths
        )
        try:
            plan = await self._agent.plan(task)
        except SourceError as exc:
            if exc.status == 404:
                raise NotFoundError("pull request or repository not found") from exc
            raise UpstreamError(exc.message) from exc
        moment = self._clock()
        now = to_iso(moment)
        job = Job(
            id=uuid.uuid4().hex,
            status="queued",
            created_at=now,
            updated_at=now,
            account=principal.account,
            pull=PullRef(
                project_id=project_id,
                repo_id=repo_id,
                number=number,
                head_sha=plan.pull.head_sha,
            ),
            context=JobContext(production_paths=plan.production_paths, test_paths=plan.test_paths),
        )
        key = reuse_key(job, self._settings.llm)
        window_start = to_iso(moment - self._settings.limits.reuse_window)
        previous = self._store.find_reusable(key, window_start)
        if previous is not None and previous.result is not None:
            job = reused(job, previous)
        else:
            self._limiter.ensure_daily_quota()
        self._store.insert(job, key)
        return job

    async def get(self, principal: Principal, job_id: str) -> Job:
        """Return a job; only its creator or a member of its project may read it."""
        job = self._store.get(job_id)
        if job is None:
            raise NotFoundError("generation job not found")
        if job.account != principal.account:
            await self._membership.ensure_member(principal, job.pull.project_id)
        return job

    async def list_for_pull(
        self, principal: Principal, project_id: int, repo_id: int, number: int, limit: int
    ) -> list[Job]:
        """Return the newest jobs of a pull request to a member of its project."""
        await self._membership.ensure_member(principal, project_id)
        return self._store.list_for_pull(project_id, repo_id, number, limit)

    def quota(self) -> Quota:
        """Return today's model request allowance."""
        return self._limiter.quota()
