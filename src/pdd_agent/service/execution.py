"""Job lifecycle: run queued jobs through the agent and record the outcome."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from pdd_agent.agents.testgen import GenerationAgent, GenerationTask
from pdd_agent.domain.errors import DomainError
from pdd_agent.domain.jobs import Job, JobError
from pdd_agent.domain.timeutil import to_iso, utc_now
from pdd_agent.storage import JobRepository

log = logging.getLogger(__name__)


class JobExecutor:
    """Moves jobs from queued to succeeded or failed."""

    def __init__(
        self,
        store: JobRepository,
        agent: GenerationAgent,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._agent = agent
        self._clock = clock

    def recover(self) -> list[str]:
        """Settle jobs left over by a previous process and return the ids to run again.

        Queued jobs have not touched the model yet and are re-queued. Running jobs may have
        spent a model request, so they fail with ``interrupted`` and the user submits again.
        """
        for job in self._store.list_by_status("running"):
            self._fail(job, "interrupted", "the agent restarted while the job was running")
        return [job.id for job in self._store.list_by_status("queued")]

    async def run(self, job_id: str) -> None:
        """Run a queued job to completion; failures are recorded on the job."""
        job = self._store.get(job_id)
        if job is None or job.status != "queued":
            return
        job = self._save(job, status="running")
        try:
            job = await self._generate(job)
        except DomainError as exc:
            self._fail(job, exc.code, exc.message)
        except Exception:
            log.exception("job %s failed unexpectedly", job.id)
            self._fail(job, "internal", "unexpected error, see the agent log")

    async def _generate(self, job: Job) -> Job:
        pull = job.pull
        output = await self._agent.run(
            GenerationTask(
                account=job.account,
                project_id=pull.project_id,
                repo_id=pull.repo_id,
                number=pull.number,
                production_paths=job.context.production_paths,
                test_paths=job.context.test_paths,
                head_sha=pull.head_sha,
                reference=job.id,
            )
        )
        context = job.context.model_copy(
            update={
                "included_files": output.included_files,
                "omitted_files": output.omitted_files,
                "truncated": output.truncated,
            }
        )
        return self._save(job, status="succeeded", result=output.result, context=context)

    def _save(self, job: Job, **changes: object) -> Job:
        updated = job.model_copy(update={**changes, "updated_at": to_iso(self._clock())})
        self._store.update(updated)
        return updated

    def _fail(self, job: Job, code: str, message: str) -> None:
        self._save(job, status="failed", error=JobError(code=code, message=message))
