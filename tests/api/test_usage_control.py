import asyncio
from email.utils import formatdate

import httpx
import pytest

from pdd_agent.config import load_settings
from pdd_agent.llm.google_genai import _retry_after
from tests.conftest import GOOD_TEST, PROD, TEST, Upstreams, auth, gemini_answer, running_api

FAST = "limits: {baseDelay: 1ms, maxDelay: 2ms, maxRetryAfter: 5s, reuseWindow: 1h}"


def settings_with(settings, tmp_path, extra: str):
    path = tmp_path / "extra.yaml"
    path.write_text(extra)
    base = tmp_path / "values.yaml"
    return load_settings([base, path])


@pytest.fixture
def fast(settings, tmp_path):
    return settings_with(settings, tmp_path, FAST)


async def test_quota_endpoint(settings, upstreams: Upstreams, tmp_path):
    limited = settings_with(
        settings, tmp_path, "limits: {requestsPerDay: 10, requestsPerMinute: 4}"
    )
    async with running_api(limited) as api:
        job = (await api.create_job()).json()["job"]
        await api.finished(job["id"])
        quota = (await api.http.get("/api/v1/quota", headers=auth())).json()
        assert quota["requestsPerDay"] == 10
        assert quota["requestsPerMinute"] == 4
        assert quota["usedToday"] == 1
        assert quota["remainingToday"] == 9
        assert quota["resetAt"].endswith("Z")
        assert (await api.http.get("/api/v1/quota")).status_code == 401


async def test_daily_quota_exhausted_rejects_creation_without_calling_gemini(
    settings, upstreams: Upstreams, tmp_path
):
    limited = settings_with(settings, tmp_path, "limits: {requestsPerDay: 1}")
    async with running_api(limited) as api:
        first = (await api.create_job()).json()["job"]
        assert (await api.finished(first["id"]))["status"] == "succeeded"
        response = await api.create_job({"productionPaths": ["src/main/java/com/acme/Other.java"]})
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "quota_exhausted"
        assert int(response.headers["Retry-After"]) > 0
        assert upstreams.gemini.call_count == 1


async def test_identical_request_reuses_result_without_gemini_call(api, upstreams: Upstreams):
    first = (await api.create_job()).json()["job"]
    original = await api.finished(first["id"])
    assert original["result"]["reused"] is False
    second = (
        await api.create_job({"productionPaths": [PROD], "testPaths": ["src/test/java"]})
    ).json()["job"]
    assert second["status"] == "succeeded"
    assert second["id"] != first["id"]
    assert second["result"]["reused"] is True
    assert second["result"]["files"] == original["result"]["files"]
    assert second["result"]["usage"] == {"promptTokens": 0, "outputTokens": 0}
    assert second["context"]["includedFiles"] == original["context"]["includedFiles"]
    assert upstreams.gemini.call_count == 1
    third = (await api.create_job()).json()["job"]
    assert third["result"]["reused"] is True and upstreams.gemini.call_count == 1


async def test_reuse_needs_same_head_context_and_window(settings, upstreams: Upstreams, tmp_path):
    async with running_api(settings) as api:
        await api.finished((await api.create_job()).json()["job"]["id"])
        other_paths = await api.create_job(
            {"productionPaths": ["src/main/java/com/acme/Other.java"]}
        )
        assert other_paths.json()["job"]["status"] == "queued"
        upstreams.head = "b" * 40
        new_head = (await api.create_job()).json()["job"]
        assert new_head["status"] == "queued"
        await api.app.state.container.services.runner.idle()
    short = settings_with(settings, tmp_path, "limits: {reuseWindow: 1ms}")
    async with running_api(short) as api:
        await asyncio.sleep(0.05)
        expired = (await api.create_job()).json()["job"]
        assert expired["status"] == "queued"


async def test_failed_jobs_are_never_reused(api, upstreams: Upstreams):
    upstreams.gemini_response = httpx.Response(400, json={"error": {"message": "bad"}})
    assert (await api.finished((await api.create_job()).json()["job"]["id"]))["status"] == "failed"
    upstreams.gemini_response = httpx.Response(
        200, json=gemini_answer([{"path": TEST, "content": GOOD_TEST}])
    )
    retry = (await api.create_job()).json()["job"]
    assert (await api.finished(retry["id"]))["result"]["reused"] is False


async def test_429_with_retry_after_is_retried_then_succeeds(fast, upstreams: Upstreams):
    answers = iter(
        [
            httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "slow"}}),
            httpx.Response(200, json=gemini_answer([{"path": TEST, "content": GOOD_TEST}], 9, 4)),
        ]
    )
    upstreams.gemini.side_effect = lambda request: next(answers)
    async with running_api(fast) as api:
        job = await api.finished((await api.create_job()).json()["job"]["id"])
        quota = (await api.http.get("/api/v1/quota", headers=auth())).json()
    assert job["status"] == "succeeded"
    assert job["result"]["usage"] == {"promptTokens": 9, "outputTokens": 4}
    assert upstreams.gemini.call_count == 2
    assert quota["usedToday"] == 2


async def test_5xx_exhausts_attempts_and_fails_the_job(fast, upstreams: Upstreams):
    upstreams.gemini_response = httpx.Response(503, json={"error": {"message": "down"}})
    async with running_api(fast) as api:
        job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "gemini_failed"
    assert upstreams.gemini.call_count == 3


async def test_persistent_429_fails_with_rate_limited(fast, upstreams: Upstreams):
    upstreams.gemini_response = httpx.Response(429, headers={"Retry-After": "0"})
    async with running_api(fast) as api:
        job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert job["error"]["code"] == "rate_limited"
    assert upstreams.gemini.call_count == 3


async def test_quota_used_up_while_job_is_queued_fails_it(
    settings, upstreams: Upstreams, tmp_path, monkeypatch
):
    limited = settings_with(settings, tmp_path, "limits: {requestsPerDay: 1}")
    async with running_api(limited) as api:
        container = api.app.state.container.services
        monkeypatch.setattr(container.runner, "submit", lambda job_id: None)
        job_id = (await api.create_job()).json()["job"]["id"]
        await container.limiter.acquire("someone-else")
        monkeypatch.undo()
        container.runner.submit(job_id)
        job = await api.finished(job_id)
    assert job["status"] == "failed"
    assert job["error"]["code"] == "quota_exhausted"
    assert upstreams.gemini.call_count == 0


def test_retry_after_parsing():
    assert _retry_after(httpx.Response(429, headers={"Retry-After": "12"})) == 12
    assert _retry_after(httpx.Response(429)) is None
    assert _retry_after(httpx.Response(429, headers={"Retry-After": "soon"})) is None
    future = formatdate(timeval=None, usegmt=True)
    assert _retry_after(httpx.Response(429, headers={"Retry-After": future})) == pytest.approx(
        0, abs=2
    )


def test_limits_defaults_and_validation(tmp_path):
    from pdd_agent.config import ConfigError

    required = "pdd: {jwtSecret: s}\ngitGateway: {internalToken: t}\nllm: {apiKey: k}\n"
    ok = tmp_path / "ok.yaml"
    ok.write_text(required)
    limits = load_settings([ok]).limits
    assert (limits.requests_per_minute, limits.requests_per_day, limits.max_attempts) == (8, 200, 3)
    assert limits.reuse_window.total_seconds() == 24 * 3600
    bad = tmp_path / "bad.yaml"
    bad.write_text(required + "limits: {requestsPerDay: 0, baseDelay: 0s, maxAttempts: x}")
    with pytest.raises(ConfigError) as err:
        load_settings([bad])
    assert "limits.requestsPerDay: must be greater than zero" in err.value.errors
    assert "limits.baseDelay: must be greater than zero" in err.value.errors
    assert "limits.maxAttempts: must be an integer" in err.value.errors
