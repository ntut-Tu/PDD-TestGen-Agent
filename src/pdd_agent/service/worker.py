"""In-process background worker pool for generation jobs."""

from __future__ import annotations

import asyncio
import logging

from pdd_agent.service.execution import JobExecutor

log = logging.getLogger(__name__)


class JobRunner:
    """Runs queued jobs with a bounded number of concurrent workers."""

    def __init__(self, executor: JobExecutor, concurrency: int) -> None:
        self._executor = executor
        self._concurrency = concurrency
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task[None]] = []

    def start(self) -> None:
        """Re-queue jobs left over by a previous process and start the workers."""
        for job_id in self._executor.recover():
            self.submit(job_id)
        self._workers = [asyncio.create_task(self._work()) for _ in range(self._concurrency)]

    def submit(self, job_id: str) -> None:
        """Queue a job for execution."""
        self._queue.put_nowait(job_id)

    async def idle(self) -> None:
        """Wait until every submitted job has been processed."""
        await self._queue.join()

    async def stop(self) -> None:
        """Cancel the workers. Jobs still running stay ``running`` and are settled on restart."""
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    async def _work(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                await self._executor.run(job_id)
            except Exception:
                log.exception("worker failed on job %s", job_id)
            finally:
                self._queue.task_done()
