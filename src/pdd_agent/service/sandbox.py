"""Runs the sandbox fix loop for a generated job and records every try."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from pdd_agent.agents.sandbox_fix import ContextFile, FixProgress, FixTask, SandboxFixAgent
from pdd_agent.domain.jobs import GeneratedFile, Job, SandboxOutcome
from pdd_agent.domain.timeutil import to_iso, utc_now
from pdd_agent.storage import JobRepository, SandboxTryRepository

log = logging.getLogger(__name__)


class SandboxExecutor:
    """Takes a job whose generation succeeded through the sandbox loop.

    The job keeps its ``succeeded`` status; ``job.sandbox`` is ``running`` while the loop
    works, every try is stored, and ``job.result.files`` always holds the latest test files.
    """

    def __init__(
        self,
        jobs: JobRepository,
        tries: SandboxTryRepository,
        agent: SandboxFixAgent,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._jobs = jobs
        self._tries = tries
        self._agent = agent
        self._clock = clock

    def recover(self) -> None:
        """Fail the sandbox result of jobs whose loop was cut short by a restart."""
        for job in self._jobs.list_sandbox_running():
            tries = len(self._tries.list_for_job(job.id))
            message = "the agent restarted while the sandbox was running"
            self._save(
                job, SandboxOutcome(status="fail", reason="internal", message=message, tries=tries)
            )

    async def run(self, job: Job, context: list[ContextFile]) -> None:
        """Run the loop for ``job`` (whose generation result is saved) and record the outcome."""
        assert job.result is not None
        current = self._save(job, SandboxOutcome(status="running"))

        def record(progress: FixProgress) -> None:
            nonlocal current
            for item in progress.tries:
                self._tries.save(job.id, item)
            running = SandboxOutcome(status="running", tries=len(progress.tries))
            current = self._save(current, running, progress.files)

        task = FixTask(
            account=job.account,
            project_id=job.pull.project_id,
            repo_id=job.pull.repo_id,
            number=job.pull.number,
            head_sha=job.pull.head_sha,
            files=job.result.files,
            context=context,
            reference=job.id,
            on_progress=record,
        )
        try:
            output = await self._agent.run(task)
        except Exception:
            log.exception("sandbox loop of job %s failed unexpectedly", job.id)
            message = "unexpected error, see the agent log"
            tries = len(self._tries.list_for_job(job.id))
            self._save(
                current,
                SandboxOutcome(status="fail", reason="internal", message=message, tries=tries),
            )
            return
        outcome = SandboxOutcome(
            status=output.status,
            reason=output.reason,
            message=output.message,
            tries=len(output.tries),
        )
        self._save(current, outcome, output.files)

    def _save(
        self, job: Job, outcome: SandboxOutcome, files: list[GeneratedFile] | None = None
    ) -> Job:
        changes: dict[str, object] = {"sandbox": outcome, "updated_at": to_iso(self._clock())}
        if files is not None and job.result is not None:
            changes["result"] = job.result.model_copy(update={"files": files})
        updated = job.model_copy(update=changes)
        self._jobs.update(updated)
        return updated
