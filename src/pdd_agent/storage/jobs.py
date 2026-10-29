"""SQLite repository of generation jobs."""

from __future__ import annotations

from pdd_agent.domain.jobs import Job, JobStatus
from pdd_agent.storage.db import Database


class JobRepository:
    """Repository of ``Job`` records."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def insert(self, job: Job, reuse_key: str | None = None) -> None:
        """Persist a new job; ``reuse_key`` identifies identical requests."""
        self._db.execute(
            "INSERT INTO jobs"
            " (id, project_id, repo_id, number, status, created_at, data, reuse_key)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job.id,
                job.pull.project_id,
                job.pull.repo_id,
                job.pull.number,
                job.status,
                job.created_at,
                _dump(job),
                reuse_key,
            ),
        )

    def find_reusable(
        self, reuse_key: str, created_since: str, sandboxed: bool = False
    ) -> Job | None:
        """Return the newest succeeded job with real Gemini output for ``reuse_key``.

        With ``sandboxed`` the job must also have a settled sandbox result that is not a
        transient failure, because the caller copies that result.
        """
        sandbox = (
            " AND json_extract(data, '$.sandbox.status') IN ('success', 'fail')"
            " AND COALESCE(json_extract(data, '$.sandbox.reason'), '') NOT IN"
            " ('sandbox_unavailable', 'source_unavailable', 'internal')"
            if sandboxed
            else ""
        )
        rows = self._db.query(
            "SELECT data FROM jobs WHERE reuse_key = ? AND status = 'succeeded'"
            " AND created_at >= ? AND json_extract(data, '$.result.reused') = 0"
            + sandbox
            + " ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (reuse_key, created_since),
        )
        return Job.model_validate_json(rows[0]["data"]) if rows else None

    def update(self, job: Job) -> None:
        """Persist the new state of an existing job."""
        self._db.execute(
            "UPDATE jobs SET status = ?, data = ? WHERE id = ?", (job.status, _dump(job), job.id)
        )

    def get(self, job_id: str) -> Job | None:
        """Return a job by id."""
        rows = self._db.query("SELECT data FROM jobs WHERE id = ?", (job_id,))
        return Job.model_validate_json(rows[0]["data"]) if rows else None

    def list_for_pull(self, project_id: int, repo_id: int, number: int, limit: int) -> list[Job]:
        """Return the newest jobs of one pull request, newest first."""
        rows = self._db.query(
            "SELECT data FROM jobs WHERE project_id = ? AND repo_id = ? AND number = ?"
            " ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (project_id, repo_id, number, limit),
        )
        return [Job.model_validate_json(r["data"]) for r in rows]

    def list_by_status(self, status: JobStatus) -> list[Job]:
        """Return every job in ``status``, oldest first."""
        rows = self._db.query(
            "SELECT data FROM jobs WHERE status = ? ORDER BY created_at, rowid", (status,)
        )
        return [Job.model_validate_json(r["data"]) for r in rows]

    def list_sandbox_running(self) -> list[Job]:
        """Return the jobs whose sandbox loop was in progress, oldest first."""
        rows = self._db.query(
            "SELECT data FROM jobs WHERE json_extract(data, '$.sandbox.status') = 'running'"
            " ORDER BY created_at, rowid"
        )
        return [Job.model_validate_json(r["data"]) for r in rows]


def _dump(job: Job) -> str:
    return job.model_dump_json(by_alias=True)
