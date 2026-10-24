"""Per-minute and per-day request limits for the language model provider."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from pdd_agent.config import LimitsConfig
from pdd_agent.domain.errors import QuotaExhaustedError
from pdd_agent.domain.quota import Quota
from pdd_agent.domain.timeutil import to_iso

# Gemini API daily quotas reset at midnight Pacific time.
PROVIDER_TZ = ZoneInfo("America/Los_Angeles")
MINUTE = 60.0


class UsageLog(Protocol):
    """Persistent record of the requests sent to the provider."""

    def reserve(self, job_id: str, requested_at: float) -> int:
        """Record a request that is about to be sent and return its usage id."""
        ...

    def complete(self, usage_id: int, http_status: int | None, prompt: int, output: int) -> None:
        """Record the outcome and token usage of a reserved request."""
        ...

    def count_since(self, since: float) -> int:
        """Return how many requests were made at or after ``since`` (epoch seconds)."""
        ...

    def timestamps_since(self, since: float) -> list[float]:
        """Return the times of the requests made after ``since``, oldest first."""
        ...


def day_bounds(now: float) -> tuple[float, float]:
    """Return the start and end (epoch seconds) of the provider day containing ``now``."""
    local = datetime.fromtimestamp(now, PROVIDER_TZ)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


class RateLimiter:
    """Counts every provider request in a persistent usage log so limits survive restarts."""

    def __init__(
        self,
        usage: UsageLog,
        config: LimitsConfig,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._usage = usage
        self._config = config
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    def quota(self) -> Quota:
        """Return the current daily usage."""
        now = self._clock()
        start, end = day_bounds(now)
        used = self._usage.count_since(start)
        per_day = self._config.requests_per_day
        return Quota(
            requests_per_day=per_day,
            used_today=used,
            remaining_today=max(per_day - used, 0),
            requests_per_minute=self._config.requests_per_minute,
            reset_at=to_iso(datetime.fromtimestamp(end, UTC)),
        )

    def ensure_daily_quota(self) -> None:
        """Raise ``QuotaExhaustedError`` when no request is left today."""
        now = self._clock()
        start, end = day_bounds(now)
        if self._usage.count_since(start) >= self._config.requests_per_day:
            raise QuotaExhaustedError(end, now)

    async def acquire(self, job_id: str) -> int:
        """Reserve one request and return its usage id.

        Waits while the per-minute limit is reached and raises ``QuotaExhaustedError`` when
        the daily limit is. Waiters are served in order because they share one lock.
        """
        async with self._lock:
            while True:
                self.ensure_daily_quota()
                now = self._clock()
                recent = self._usage.timestamps_since(now - MINUTE)
                limit = self._config.requests_per_minute
                if len(recent) < limit:
                    return self._usage.reserve(job_id, now)
                await self._sleep(recent[len(recent) - limit] + MINUTE - now)

    def complete(self, usage_id: int, http_status: int | None, prompt: int, output: int) -> None:
        """Record the outcome and token usage of a reserved request."""
        self._usage.complete(usage_id, http_status, prompt, output)
