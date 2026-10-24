"""SQLite repository of the language model request log."""

from __future__ import annotations

from pdd_agent.storage.db import Database


class UsageRepository:
    """Records every provider request; implements ``pdd_agent.llm.usage.UsageLog``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def reserve(self, job_id: str, requested_at: float) -> int:
        """Record a request that is about to be sent and return its usage id."""
        cursor = self._db.execute(
            "INSERT INTO gemini_usage (job_id, requested_at) VALUES (?, ?)", (job_id, requested_at)
        )
        return int(cursor.lastrowid or 0)

    def complete(self, usage_id: int, http_status: int | None, prompt: int, output: int) -> None:
        """Record the outcome and token usage of a reserved request."""
        self._db.execute(
            "UPDATE gemini_usage SET http_status = ?, prompt_tokens = ?, output_tokens = ?"
            " WHERE id = ?",
            (http_status, prompt, output, usage_id),
        )

    def count_since(self, since: float) -> int:
        """Return how many requests were made at or after ``since`` (epoch seconds)."""
        row = self._db.query(
            "SELECT COUNT(*) AS n FROM gemini_usage WHERE requested_at >= ?", (since,)
        )
        return int(row[0]["n"])

    def timestamps_since(self, since: float) -> list[float]:
        """Return the times of the requests made after ``since``, oldest first."""
        rows = self._db.query(
            "SELECT requested_at FROM gemini_usage WHERE requested_at > ? ORDER BY requested_at",
            (since,),
        )
        return [r["requested_at"] for r in rows]
