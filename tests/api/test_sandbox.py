"""Jobs with the sandbox fix loop, against a fake sandbox-controller and a fake Gemini."""

import json
from pathlib import Path

import httpx
import pytest

from pdd_agent.config import Settings, load_settings
from pdd_agent.domain.jobs import Job, JobContext, JobResult, PullRef, SandboxOutcome, Usage
from pdd_agent.storage import Database, JobRepository, SandboxTryRepository
from tests.conftest import (
    BACKEND,
    GATEWAY,
    GEMINI,
    GOOD_TEST,
    SECRET,
    TEST,
    Upstreams,
    auth,
    gemini_answer,
    running_api,
)

CONTROLLER = "http://sandbox.test"
FIXED_TEST = GOOD_TEST.replace("assertEquals(3", "assertEquals(3L + 0")
PASS = {"phase": "Succeeded", "message": "all tests passed"}
FAIL = {
    "phase": "Failed",
    "reason": "tests_failed",
    "message": "1 test failed",
    "result": {
        "compiled": True,
        "tests": [{"class": "com.acme.CalcTest", "passed": 0, "failed": 1}],
        "output": "expected 3 but was 4",
    },
}


class FakeController:
    """Creates runs and answers them with scripted statuses (the last one repeats)."""

    def __init__(self, router, *statuses: dict | int) -> None:
        self.statuses = list(statuses)
        self.runs: dict[str, dict] = {}
        self.posted: list[httpx.Request] = []
        router.post(f"{CONTROLLER}/internal/v1/runs").mock(side_effect=self._create)
        router.get(url__regex=rf"{CONTROLLER}/internal/v1/runs/.+").mock(side_effect=self._get)

    def _create(self, request: httpx.Request) -> httpx.Response:
        self.posted.append(request)
        outcome = self.statuses[min(len(self.posted), len(self.statuses)) - 1]
        if isinstance(outcome, int):
            return httpx.Response(outcome, json={"error": {"code": "x", "message": "y"}})
        run = {
            "id": f"run_{len(self.posted)}",
            "spec": json.loads(request.content)["spec"],
            "status": {"phase": "Pending"},
        }
        self.runs[run["id"]] = {**run, "status": outcome}
        return httpx.Response(201, json=run)

    def _get(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.runs[request.url.path.rsplit("/", 1)[1]])

    def spec(self, index: int) -> dict:
        return json.loads(self.posted[index].content)["spec"]


def fix(content: str = FIXED_TEST) -> httpx.Response:
    answer = {"verdict": "fix", "reason": "", "files": [{"path": TEST, "content": content}]}
    return httpx.Response(
        200, json=gemini_answer([]) | {"candidates": [_candidate(json.dumps(answer))]}
    )


def _candidate(text: str) -> dict:
    return {"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}


def script_gemini(upstreams: Upstreams, *later: httpx.Response) -> None:
    """The first call generates tests; later calls answer the fix requests in order."""
    queue = list(later)
    first = upstreams.gemini_response

    def answer(request: httpx.Request) -> httpx.Response:
        return queue.pop(0) if queue and upstreams.gemini.call_count > 0 else first

    upstreams.gemini.side_effect = answer


def sandbox_settings(tmp_path: Path, extra: str = "") -> Settings:
    config = tmp_path / "values.yaml"
    config.write_text(
        f"""
pdd: {{jwtSecret: {SECRET}, backend: {{url: "{BACKEND}"}}}}
gitGateway: {{url: "{GATEWAY}", internalToken: internal}}
llm: {{apiKey: key, baseURL: "{GEMINI}"}}
storage: {{dataDir: {tmp_path / "data"}}}
sandbox:
  controllerURL: "{CONTROLLER}"
  internalToken: sandbox-secret
  maxTries: 3
  pollInterval: 10ms
{extra}
"""
    )
    return load_settings([config])


@pytest.fixture
def sandbox(tmp_path: Path) -> Settings:
    return sandbox_settings(tmp_path)


def stored_tries(settings: Settings, job_id: str):
    db = Database(Path(settings.storage.data_dir) / "test-agent.db")
    try:
        return SandboxTryRepository(db).list_for_job(job_id)
    finally:
        db.close()


async def finished_job(settings: Settings) -> tuple[dict, str]:
    async with running_api(settings) as api:
        job_id = (await api.create_job()).json()["job"]["id"]
        return await api.finished(job_id), job_id


async def test_tests_that_pass_give_sandbox_success(sandbox, upstreams: Upstreams):
    controller = FakeController(upstreams.router, PASS)
    job, job_id = await finished_job(sandbox)

    assert job["status"] == "succeeded"
    assert job["sandbox"] == {
        "status": "success",
        "reason": None,
        "message": "all generated tests passed",
        "tries": 1,
    }
    assert upstreams.gemini.call_count == 1
    request = controller.posted[0]
    assert request.headers["X-Internal-Token"] == "sandbox-secret"
    spec = controller.spec(0)
    assert (spec["account"], spec["projectId"], spec["repoId"]) == ("alice", 7, 3)
    assert spec["headSha"] == "a" * 40 and spec["testClasses"] == ["com.acme.CalcTest"]
    assert spec["files"] == [{"path": TEST, "content": GOOD_TEST}]
    [kept] = stored_tries(sandbox, job_id)
    assert (kept.number, kept.run_id, kept.phase) == (1, "run_1", "Succeeded")


async def test_failed_tests_are_fixed_and_the_files_show_the_latest_content(
    sandbox, upstreams: Upstreams
):
    controller = FakeController(upstreams.router, FAIL, PASS)
    script_gemini(upstreams, fix())
    job, job_id = await finished_job(sandbox)

    assert job["sandbox"]["status"] == "success" and job["sandbox"]["tries"] == 2
    assert [f["content"] for f in job["result"]["files"]] == [FIXED_TEST]
    assert controller.spec(1)["files"] == [{"path": TEST, "content": FIXED_TEST}]
    fix_request = upstreams.gemini.calls.last.request.read().decode()
    assert "expected 3 but was 4" in fix_request and "tests_failed" in fix_request
    tries = stored_tries(sandbox, job_id)
    assert [(t.number, t.phase, t.reason) for t in tries] == [
        (1, "Failed", "tests_failed"),
        (2, "Succeeded", None),
    ]
    assert "0 passed" in tries[0].summary
    assert "tries" not in json.dumps(job["result"])


async def test_max_tries_ends_as_fail(sandbox, upstreams: Upstreams):
    FakeController(upstreams.router, FAIL)
    script_gemini(upstreams, fix(), fix(FIXED_TEST + "\n"))
    job, job_id = await finished_job(sandbox)

    assert job["status"] == "succeeded"
    assert job["sandbox"]["status"] == "fail" and job["sandbox"]["reason"] == "max_tries"
    assert job["sandbox"]["tries"] == 3 and len(stored_tries(sandbox, job_id)) == 3
    assert upstreams.gemini.call_count == 3


async def test_production_bug_verdict_is_reported_with_its_reason(sandbox, upstreams: Upstreams):
    FakeController(upstreams.router, FAIL)
    verdict = {"verdict": "production_bug", "reason": "add() subtracts"}
    script_gemini(
        upstreams,
        httpx.Response(200, json={"candidates": [_candidate(json.dumps(verdict))]}),
    )
    job, _ = await finished_job(sandbox)
    assert job["sandbox"] == {
        "status": "fail",
        "reason": "production_bug",
        "message": "add() subtracts",
        "tries": 1,
    }


async def test_unreachable_controller_keeps_the_generated_tests(sandbox, upstreams: Upstreams):
    FakeController(upstreams.router, 503)
    job, _ = await finished_job(sandbox)
    assert job["status"] == "succeeded" and job["result"]["files"][0]["content"] == GOOD_TEST
    assert job["sandbox"]["status"] == "fail"
    assert job["sandbox"]["reason"] == "sandbox_unavailable"
    assert job["sandbox"]["tries"] == 0


async def test_error_phase_reason_is_shown(sandbox, upstreams: Upstreams):
    status = {"phase": "Error", "reason": "unsupported_project", "message": "no pom.xml"}
    FakeController(upstreams.router, status)
    job, _ = await finished_job(sandbox)
    assert job["sandbox"]["reason"] == "unsupported_project"
    assert job["sandbox"]["message"] == "no pom.xml"


async def test_exhausted_quota_mid_loop_fails_the_sandbox_only(tmp_path, upstreams: Upstreams):
    settings = sandbox_settings(tmp_path, "limits: {requestsPerDay: 1}")
    FakeController(upstreams.router, FAIL, PASS)
    job, _ = await finished_job(settings)
    assert job["status"] == "succeeded"
    assert job["sandbox"]["status"] == "fail" and job["sandbox"]["reason"] == "internal"
    assert "quota" in job["sandbox"]["message"] and job["sandbox"]["tries"] == 1


async def test_disabled_sandbox_leaves_the_field_null(api, upstreams: Upstreams):
    job = await api.finished((await api.create_job()).json()["job"]["id"])
    assert "sandbox" in job and job["sandbox"] is None


async def test_job_is_listed_with_the_sandbox_result(sandbox, upstreams: Upstreams):
    FakeController(upstreams.router, PASS)
    async with running_api(sandbox) as api:
        job_id = (await api.create_job()).json()["job"]["id"]
        await api.finished(job_id)
        listing = await api.http.get(
            "/api/v1/projects/7/repos/3/pulls/5/generations", headers=auth()
        )
    assert listing.json()["jobs"][0]["sandbox"]["status"] == "success"


async def test_identical_request_reuses_the_sandboxed_result(sandbox, upstreams: Upstreams):
    controller = FakeController(upstreams.router, PASS)
    async with running_api(sandbox) as api:
        first = await api.finished((await api.create_job()).json()["job"]["id"])
        second = (await api.create_job()).json()["job"]
    assert second["status"] == "succeeded" and second["result"]["reused"] is True
    assert second["sandbox"] == first["sandbox"]
    assert len(controller.posted) == 1 and upstreams.gemini.call_count == 1


async def test_reuse_skips_jobs_without_a_settled_sandbox_result(sandbox, upstreams: Upstreams):
    FakeController(upstreams.router, 503, PASS)
    async with running_api(sandbox) as api:
        first = await api.finished((await api.create_job()).json()["job"]["id"])
        second_id = (await api.create_job()).json()["job"]["id"]
        second = await api.finished(second_id)
    assert first["sandbox"]["reason"] == "sandbox_unavailable"
    assert second["result"]["reused"] is False and second["sandbox"]["status"] == "success"


async def test_restart_fails_a_sandbox_loop_that_was_running(sandbox, upstreams: Upstreams):
    db = Database(Path(sandbox.storage.data_dir) / "test-agent.db")
    job = Job(
        id="seed",
        status="succeeded",
        created_at="2026-01-01T00:00:00.000Z",
        updated_at="2026-01-01T00:00:00.000Z",
        account="alice",
        pull=PullRef(project_id=7, repo_id=3, number=5, head_sha="a" * 40),
        context=JobContext(production_paths=[], test_paths=[]),
        result=JobResult(files=[], model="m", usage=Usage()),
        sandbox=SandboxOutcome(status="running", tries=2),
    )
    JobRepository(db).insert(job)
    db.close()
    async with running_api(sandbox) as api:
        restarted = await api.finished("seed")
    assert restarted["sandbox"] == {
        "status": "fail",
        "reason": "internal",
        "message": "the agent restarted while the sandbox was running",
        "tries": 0,
    }
