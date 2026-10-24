"""SQLite connection with schema migrations."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from pdd_agent.storage.migrations import MIGRATIONS


class Database:
    """A SQLite connection shared by the repositories, guarded by a lock.

    Statements are short local queries, so they run directly on the event loop thread.
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        self._migrate()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Run one statement and return its cursor."""
        with self.lock:
            return self._conn.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Run a query and return every row."""
        with self.lock:
            return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        """Close the connection."""
        with self.lock:
            self._conn.close()

    def _migrate(self) -> None:
        with self.lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
                self._conn.executescript(f"BEGIN;{script}PRAGMA user_version = {number};COMMIT;")
