"""SQLite repository of the sandbox tries of generation jobs."""

from __future__ import annotations

from pdd_agent.domain.jobs import SandboxTry
from pdd_agent.storage.db import Database


class SandboxTryRepository:
    """Keeps every sandbox try of a job, keyed by job id and try number."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, job_id: str, record: SandboxTry) -> None:
        """Insert a try, or overwrite the one with the same number."""
        self._db.execute(
            "INSERT OR REPLACE INTO sandbox_tries"
            " (job_id, try_number, run_id, phase, reason, summary) VALUES (?, ?, ?, ?, ?, ?)",
            (job_id, record.number, record.run_id, record.phase, record.reason, record.summary),
        )

    def list_for_job(self, job_id: str) -> list[SandboxTry]:
        """Return the tries of a job in order."""
        rows = self._db.query(
            "SELECT try_number, run_id, phase, reason, summary FROM sandbox_tries"
            " WHERE job_id = ? ORDER BY try_number",
            (job_id,),
        )
        return [
            SandboxTry(
                number=r["try_number"],
                run_id=r["run_id"],
                phase=r["phase"],
                reason=r["reason"],
                summary=r["summary"],
            )
            for r in rows
        ]
