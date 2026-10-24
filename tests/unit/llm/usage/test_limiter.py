from datetime import UTC, datetime

import pytest

from pdd_agent.config import LimitsConfig
from pdd_agent.domain.errors import QuotaExhaustedError
from pdd_agent.llm.usage import RateLimiter, day_bounds
from pdd_agent.storage import Database, UsageRepository


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


NOON_UTC = datetime(2026, 10, 24, 12, 0, tzinfo=UTC).timestamp()


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "t.db")
    yield database
    database.close()


def limiter(db, clock, per_minute=2, per_day=5):
    config = LimitsConfig(requests_per_minute=per_minute, requests_per_day=per_day)
    return RateLimiter(UsageRepository(db), config, clock, clock.sleep)


async def test_per_minute_limit_waits_for_the_oldest_request_to_age_out(db):
    clock = Clock(NOON_UTC)
    rate = limiter(db, clock)
    await rate.acquire("j")
    clock.now += 10
    await rate.acquire("j")
    clock.now += 5
    await rate.acquire("j")
    assert clock.slept == [pytest.approx(45)]
    assert rate.quota().used_today == 3


async def test_requests_older_than_a_minute_do_not_count(db):
    clock = Clock(NOON_UTC)
    rate = limiter(db, clock)
    await rate.acquire("j")
    await rate.acquire("j")
    clock.now += 61
    await rate.acquire("j")
    assert clock.slept == []


async def test_daily_limit_raises_with_reset_time(db):
    clock = Clock(NOON_UTC)
    rate = limiter(db, clock, per_minute=10, per_day=2)
    await rate.acquire("j")
    await rate.acquire("j")
    with pytest.raises(QuotaExhaustedError) as err:
        await rate.acquire("j")
    assert "resets at 2026-10-25T07:00:00.000Z" in str(err.value)
    assert err.value.retry_after == 19 * 3600 + 1
    with pytest.raises(QuotaExhaustedError):
        rate.ensure_daily_quota()
    quota = rate.quota()
    assert (quota.used_today, quota.remaining_today) == (2, 0)


async def test_counter_resets_at_midnight_pacific_and_persists_across_instances(db):
    clock = Clock(NOON_UTC)
    await limiter(db, clock, per_minute=10, per_day=1).acquire("j")
    second = limiter(db, clock, per_minute=10, per_day=1)
    with pytest.raises(QuotaExhaustedError):
        second.ensure_daily_quota()
    clock.now = day_bounds(NOON_UTC)[1] + 1
    second.ensure_daily_quota()
    assert second.quota().used_today == 0


def test_day_bounds_follow_pacific_time_including_dst_change():
    start, end = day_bounds(NOON_UTC)
    assert datetime.fromtimestamp(start, UTC) == datetime(2026, 10, 24, 7, 0, tzinfo=UTC)
    assert datetime.fromtimestamp(end, UTC) == datetime(2026, 10, 25, 7, 0, tzinfo=UTC)
    _, fall_back_end = day_bounds(datetime(2026, 11, 1, 12, 0, tzinfo=UTC).timestamp())
    assert datetime.fromtimestamp(fall_back_end, UTC) == datetime(2026, 11, 2, 8, 0, tzinfo=UTC)


async def test_complete_records_status_and_tokens(db):
    rate = limiter(db, Clock(NOON_UTC))
    usage_id = await rate.acquire("job-1")
    rate.complete(usage_id, 200, 12, 34)
    row = db.query("SELECT * FROM gemini_usage")[0]
    assert (row["job_id"], row["http_status"], row["prompt_tokens"], row["output_tokens"]) == (
        "job-1",
        200,
        12,
        34,
    )
