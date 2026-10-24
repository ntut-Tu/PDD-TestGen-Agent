"""Language model request allowance."""

from __future__ import annotations

from pdd_agent.domain.jobs import CamelModel


class Quota(CamelModel):
    """Daily and per-minute language model request allowance."""

    requests_per_day: int
    used_today: int
    remaining_today: int
    requests_per_minute: int
    reset_at: str
