from pathlib import Path

import httpx

from pdd_agent.domain.jobs import Job, JobContext, PullRef
from pdd_agent.storage import Database, JobRepository
from tests.conftest import (
    GOOD_TEST,
    PROD,
    TEST,
    Upstreams,
    auth,
    gemini_answer,
    make_token,
    running_api,
)

LIST_URL = "/api/v1/projects/7/repos/3/pulls/5/generations"


async def test_job_lifecycle_success(api, upstreams: Upstreams):
    response = await api.create_job()
    assert response.status_code == 202
    created = response.json()["job"]
    assert created["status"] == "queued"
    assert created["pull"] == {"projectId": 7, "repoId": 3, "number": 5, "headSha": "a" * 40}
    assert created["account"] == "alice"
    assert created["context"]["productionPaths"] == [PROD]
    assert created["context"]["testPaths"] == ["src/test/java"]

    job = await api.finished(created["id"])
    assert job["status"] == "succeeded"
    assert job["error"] is None
    assert job["result"]["model"] == "gemini-3.8-flash"
    assert job["result"]["usage"] == {"promptTokens": 100, "outputTokens": 50}
    assert job["result"]["reused"] is False
    [file] = job["result"]["files"]
    assert file == {"path": TEST, "content": GOOD_TEST, "syntaxValid": True, "syntaxError": None}
    assert job["context"]["includedFiles"] == [PROD, TEST]
    assert job["context"]["omittedFiles"] == []
    assert job["context"]["truncated"] is False

    prompt = upstreams.gemini.calls.last.request.read().decode()
    for part in ("@@ +1", "class Calc", "class CalcTest", "JUnit 5", "Mockito is available"):
        assert part in prompt


async def test_agent_only_talks_to_gateway_backend_and_gemini(api, upstreams: Upstreams):
    job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert job["status"] == "succeeded"
    hosts = {call.request.url.host for call in upstreams.router.calls}
    assert hosts == {"gateway.test", "backend.test", "gemini.test"}
    internal = [c.request for c in upstreams.router.calls if c.request.url.host == "gateway.test"]
    assert all(r.headers["X-Internal-Token"] == "internal" for r in internal)
    assert all(r.url.params["account"] == "alice" for r in internal)


async def test_generated_file_with_bad_syntax_and_bad_path_are_marked(api, upstreams: Upstreams):
    files = [
        {"path": TEST, "content": "package com.acme;\nclass CalcTest { void x( {}"},
        {"path": PROD, "content": "package com.acme; class Calc {}"},
    ]
    upstreams.gemini_response = httpx.Response(200, json=gemini_answer(files))
    job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert job["status"] == "succeeded"
    first, second = job["result"]["files"]
    assert first["syntaxValid"] is False and first["syntaxError"]
    assert second["syntaxValid"] is False and "src/test" in second["syntaxError"]


async def test_unsupported_language_is_rejected_synchronously(api, upstreams: Upstreams):
    upstreams.tree_entries = [{"path": "package.json", "type": "blob"}]
    response = await api.create_job()
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_language"
    assert "Maven or Gradle" in response.json()["error"]["message"]
    assert upstreams.gemini.call_count == 0
    assert (await api.http.get(LIST_URL, headers=auth())).json() == {"jobs": []}


async def test_invalid_selection_is_rejected(api):
    response = await api.create_job({"productionPaths": ["src/main/java/Nope.java"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "path_not_found"
    response = await api.create_job({"productionPaths": ["../etc"]})
    assert response.json()["error"]["code"] == "invalid_path"


async def test_directory_selection(api, upstreams: Upstreams):
    created = await api.create_job({"productionPaths": ["src/main"], "testPaths": ["src/test"]})
    job = await api.finished(created.json()["job"]["id"])
    assert job["context"]["includedFiles"] == [
        PROD,
        "src/main/java/com/acme/Other.java",
        TEST,
    ]


async def test_pull_not_found_and_gateway_failure(api, upstreams: Upstreams):
    gateway_files = upstreams.pull_route
    gateway_files.side_effect = lambda request: httpx.Response(404)
    assert (await api.create_job()).status_code == 404
    gateway_files.side_effect = lambda request: httpx.Response(500)
    response = await api.create_job()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_failed"


async def test_job_failures_are_recorded(api, upstreams: Upstreams):
    upstreams.gemini_response = httpx.Response(500, json={"error": {"message": "boom"}})
    job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "gemini_failed"
    assert "boom" in job["error"]["message"]
    assert job["result"] is None


async def test_job_fails_when_pull_head_moves(api, upstreams: Upstreams):
    created = (await api.create_job()).json()["job"]
    upstreams.head = "b" * 40
    job = await api.finished(created["id"])
    assert job["error"]["code"] == "pull_changed"


async def test_job_fails_when_gateway_breaks_during_run(api, upstreams: Upstreams):
    created = (await api.create_job()).json()["job"]
    upstreams.pull_route.side_effect = lambda request: httpx.Response(500)
    assert (await api.finished(created["id"]))["error"]["code"] == "gateway_failed"


async def test_unexpected_error_marks_job_failed(api, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(api.app.state.container.services.executor, "_generate", boom)
    job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert job["error"]["code"] == "internal"


async def test_list_is_newest_first_and_limited(api):
    ids = [(await api.create_job()).json()["job"]["id"] for _ in range(3)]
    await api.app.state.container.services.runner.idle()
    jobs = (await api.http.get(LIST_URL + "?limit=2", headers=auth())).json()["jobs"]
    assert [j["id"] for j in jobs] == [ids[2], ids[1]]
    assert (await api.http.get(LIST_URL + "?limit=0", headers=auth())).status_code == 400


async def test_get_job_access_rules(api, upstreams: Upstreams):
    job_id = (await api.create_job()).json()["job"]["id"]
    await api.finished(job_id)
    url = f"/api/v1/generations/{job_id}"
    assert (await api.http.get(url, headers=auth("bob"))).status_code == 200  # member of project 7
    upstreams.projects = [{"id": 99}]
    other = {"Authorization": f"Bearer {make_token('carol')}"}
    response = await api.http.get(url, headers=other)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert (await api.http.get(url, headers=auth())).status_code == 200  # creator
    assert (await api.http.get("/api/v1/generations/nope", headers=auth())).status_code == 404


async def test_auth_errors(api, upstreams: Upstreams):
    for headers in (
        {},
        {"Authorization": "Bearer junk"},
        {"Authorization": f"Bearer {make_token(exp_in=-5)}"},
    ):
        for response in (
            await api.create_job(headers=headers or {"X-None": "1"}),
            await api.http.get(LIST_URL, headers=headers),
            await api.http.get("/api/v1/generations/x", headers=headers),
        ):
            assert response.status_code == 401
            assert response.json()["error"]["code"] == "unauthorized"
    upstreams.projects = [{"id": 1}]
    assert (await api.create_job()).status_code == 403
    assert (await api.http.get(LIST_URL, headers=auth())).status_code == 403
    assert upstreams.gemini.call_count == 0


async def test_misc_endpoints(api):
    assert (await api.http.get("/healthz")).json() == {"status": "ok"}
    missing = await api.http.get("/nowhere")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
    bad = await api.http.post(LIST_URL, json={"productionPaths": "x"}, headers=auth())
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_request"
    wrong = await api.http.put(LIST_URL, headers=auth())
    assert wrong.status_code == 405


async def test_jobs_survive_restart(settings, upstreams: Upstreams):
    async with running_api(settings) as first:
        job_id = (await first.create_job()).json()["job"]["id"]
        before = await first.finished(job_id)
    async with running_api(settings) as second:
        after = (await second.http.get(f"/api/v1/generations/{job_id}", headers=auth())).json()
    assert after["job"] == before
    assert before["status"] == "succeeded"


def _seed(settings, status: str) -> str:
    db = Database(Path(settings.storage.data_dir) / "test-agent.db")
    job = Job(
        id=f"seed-{status}",
        status=status,
        created_at="2026-01-01T00:00:00.000Z",
        updated_at="2026-01-01T00:00:00.000Z",
        account="alice",
        pull=PullRef(project_id=7, repo_id=3, number=5, head_sha="a" * 40),
        context=JobContext(production_paths=[PROD], test_paths=["src/test/java"]),
    )
    JobRepository(db).insert(job)
    db.close()
    return job.id


async def test_restart_requeues_queued_and_fails_running_jobs(settings, upstreams: Upstreams):
    queued = _seed(settings, "queued")
    running = _seed(settings, "running")
    async with running_api(settings) as api:
        await api.app.state.container.services.runner.idle()
        assert (await api.finished(queued))["status"] == "succeeded"
        interrupted = await api.finished(running)
    assert interrupted["status"] == "failed"
    assert interrupted["error"]["code"] == "interrupted"
    assert upstreams.gemini.call_count == 1
