import random

import pytest

from pdd_agent.config import LimitsConfig
from pdd_agent.domain.errors import LlmError
from pdd_agent.llm.usage import ManagedLlmClient, RateLimiter, is_retryable
from pdd_agent.ports.llm import LlmPrompt, LlmResponse, LlmUsage
from pdd_agent.storage import Database, UsageRepository

OK = LlmResponse("answer", LlmUsage(prompt_tokens=7, output_tokens=3), "m")


class ScriptedClient:
    """Returns or raises the scripted outcomes in order."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


OPEN: list[Database] = []


@pytest.fixture(autouse=True)
def close_databases():
    yield
    while OPEN:
        OPEN.pop().close()


class Harness:
    def __init__(self, tmp_path, *outcomes, **limits):
        self.db = Database(tmp_path / "t.db")
        OPEN.append(self.db)
        self.sleeps: list[float] = []
        self.client = ScriptedClient(*outcomes)
        config = LimitsConfig(**{"requests_per_minute": 100, "requests_per_day": 100, **limits})
        self.limiter = RateLimiter(UsageRepository(self.db), config)
        self.gemini = ManagedLlmClient(
            self.client, self.limiter, config, self._sleep, random.Random(1)
        )

    async def _sleep(self, seconds):
        self.sleeps.append(seconds)

    def rows(self):
        return [
            tuple(r)
            for r in self.db.query(
                "SELECT http_status, prompt_tokens, output_tokens FROM gemini_usage ORDER BY id"
            )
        ]

    async def run(self):
        return await self.gemini.generate(LlmPrompt("s", "u", reference="job"))


def http(status, retry_after=None):
    return LlmError("gemini_failed", f"status {status}", status=status, retry_after=retry_after)


async def test_success_records_usage_per_job(tmp_path):
    h = Harness(tmp_path, OK)
    assert await h.run() is OK
    assert h.rows() == [(200, 7, 3)]
    assert h.sleeps == []


async def test_429_with_retry_after_waits_exactly_that_long(tmp_path):
    h = Harness(tmp_path, http(429, retry_after=7), OK)
    assert await h.run() is OK
    assert h.sleeps == [7]
    assert h.client.calls == 2
    assert h.rows() == [(429, 0, 0), (200, 7, 3)]


async def test_retry_after_above_limit_is_not_waited_for(tmp_path):
    h = Harness(tmp_path, http(429, retry_after=3600), OK)
    with pytest.raises(LlmError) as err:
        await h.run()
    assert err.value.code == "rate_limited"
    assert h.client.calls == 1 and h.sleeps == []


async def test_5xx_is_retried_with_exponential_full_jitter(tmp_path):
    h = Harness(tmp_path, http(503), http(500), OK, max_attempts=3)
    assert await h.run() is OK
    first, second = h.sleeps
    assert 0 <= first <= 1
    assert 0 <= second <= 2
    assert h.client.calls == 3


async def test_backoff_ceiling_is_capped_by_max_delay(tmp_path):
    from datetime import timedelta

    outcomes = [http(500)] * 4 + [OK]
    h = Harness(tmp_path, *outcomes, max_attempts=5, max_delay=timedelta(seconds=2))
    await h.run()
    assert all(s <= 2 for s in h.sleeps) and len(h.sleeps) == 4


async def test_gives_up_after_max_attempts(tmp_path):
    h = Harness(tmp_path, http(502), http(502), http(502), OK, max_attempts=3)
    with pytest.raises(LlmError) as err:
        await h.run()
    assert err.value.code == "gemini_failed" and err.value.status == 502
    assert h.client.calls == 3
    assert len(h.rows()) == 3


async def test_persistent_429_ends_as_rate_limited(tmp_path):
    h = Harness(tmp_path, http(429), http(429), max_attempts=2)
    with pytest.raises(LlmError) as err:
        await h.run()
    assert err.value.code == "rate_limited"
    assert "2 attempt" in err.value.message


async def test_client_errors_and_invalid_answers_are_not_retried(tmp_path):
    for error in (http(400), LlmError("invalid_response", "bad json")):
        h = Harness(tmp_path, error)
        with pytest.raises(LlmError):
            await h.run()
        assert h.client.calls == 1


async def test_unavailable_model_is_not_retried_and_counts_one_request(tmp_path):
    error = LlmError("model_unavailable", "gone", status=404)
    h = Harness(tmp_path, error, OK)
    with pytest.raises(LlmError) as err:
        await h.run()
    assert err.value.code == "model_unavailable"
    assert h.client.calls == 1 and h.sleeps == []
    assert len(h.rows()) == 1


async def test_network_errors_are_retried(tmp_path):
    h = Harness(tmp_path, LlmError("gemini_failed", "timeout"), OK)
    assert await h.run() is OK


async def test_quota_exhaustion_stops_before_calling_gemini(tmp_path):
    h = Harness(tmp_path, http(500), OK, requests_per_day=1)
    with pytest.raises(LlmError) as err:
        await h.run()
    assert err.value.code == "quota_exhausted"
    assert h.client.calls == 1


async def test_usage_is_recorded_under_the_prompt_reference(tmp_path):
    h = Harness(tmp_path, OK)
    await h.run()
    [row] = h.db.query("SELECT job_id FROM gemini_usage")
    assert row["job_id"] == "job"


def test_is_retryable():
    assert is_retryable(http(429)) and is_retryable(http(500)) and is_retryable(http(503))
    assert not is_retryable(http(400)) and not is_retryable(http(404))
    assert is_retryable(LlmError("gemini_failed", "net"))
    assert not is_retryable(LlmError("invalid_response", "x"))
