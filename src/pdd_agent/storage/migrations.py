"""Schema migrations, applied in order; ``PRAGMA user_version`` tracks the applied count.

Never edit an entry that has shipped: append a new one.
"""

from __future__ import annotations

MIGRATIONS: list[str] = [
    """
    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        project_id INTEGER NOT NULL,
        repo_id INTEGER NOT NULL,
        number INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX jobs_by_pull ON jobs (project_id, repo_id, number, created_at);
    CREATE INDEX jobs_by_status ON jobs (status, created_at);
    """,
    """
    ALTER TABLE jobs ADD COLUMN reuse_key TEXT;
    CREATE INDEX jobs_by_reuse_key ON jobs (reuse_key, created_at);
    CREATE TABLE gemini_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL,
        requested_at REAL NOT NULL,
        http_status INTEGER,
        prompt_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX gemini_usage_by_time ON gemini_usage (requested_at);
    """,
]
