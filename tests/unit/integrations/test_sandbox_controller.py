import json

import httpx
import pytest

from pdd_agent.domain.errors import SandboxError
from pdd_agent.domain.sandbox import SandboxFile, SandboxSpec
from pdd_agent.integrations.sandbox_controller import SandboxControllerClient

BASE = "http://sandbox.test"
SPEC = SandboxSpec(
    account="alice",
    project_id=7,
    repo_id=3,
    head_sha="a" * 40,
    files=[SandboxFile(path="src/test/java/a/BTest.java", content="class BTest {}")],
    test_classes=["a.BTest"],
)
RUN = {
    "id": "run_1",
    "spec": {
        "account": "alice",
        "projectId": 7,
        "repoId": 3,
        "headSha": "a" * 40,
        "files": [{"path": "src/test/java/a/BTest.java", "content": "class BTest {}"}],
        "testClasses": ["a.BTest"],
    },
    "status": {
        "phase": "Failed",
        "reason": "tests_failed",
        "message": "1 failed",
        "buildTool": "maven",
        "result": {
            "compiled": True,
            "tests": [{"class": "a.BTest", "passed": 3, "failed": 1, "errors": 0, "skipped": 0}],
            "output": "boom",
        },
        "conditions": [{"type": "Prepared", "status": True}],
        "createdAt": "2026-10-24T10:00:00Z",
    },
    "deletionRequested": False,
}


def client_for(handler) -> tuple[SandboxControllerClient, httpx.AsyncClient]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SandboxControllerClient(http, BASE + "/", "secret-token"), http


async def test_submit_posts_the_spec_with_the_token():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=RUN)

    client, http = client_for(handler)
    run = await client.submit(SPEC)
    await http.aclose()

    [request] = seen
    assert (request.method, str(request.url)) == ("POST", BASE + "/internal/v1/runs")
    assert request.headers["X-Internal-Token"] == "secret-token"
    assert json.loads(request.content) == {"spec": RUN["spec"]}
    assert run.id == "run_1" and run.finished
    assert run.status.reason == "tests_failed" and run.status.build_tool == "maven"
    [cls] = run.status.result.tests
    assert (cls.class_name, cls.passed, cls.failed) == ("a.BTest", 3, 1)


async def test_get_and_delete():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/runs/run_1"
        if request.method == "GET":
            return httpx.Response(200, json=RUN)
        return httpx.Response(202)

    client, http = client_for(handler)
    assert (await client.get("run_1")).id == "run_1"
    await client.delete("run_1")
    await http.aclose()


async def test_delete_of_an_unknown_run_is_fine():
    client, http = client_for(lambda r: httpx.Response(404, json={"error": {}}))
    await client.delete("gone")
    await http.aclose()


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "internal"), (404, "internal"), (500, "internal"), (502, "sandbox_unavailable")],
)
async def test_status_errors_are_mapped(status, code):
    client, http = client_for(lambda r: httpx.Response(status, text="secret-token body"))
    with pytest.raises(SandboxError) as err:
        await client.get("run_1")
    assert err.value.code == code
    assert str(status) in err.value.message and "secret-token" not in err.value.message
    await http.aclose()


async def test_delete_with_an_unexpected_status_fails():
    client, http = client_for(lambda r: httpx.Response(503))
    with pytest.raises(SandboxError) as err:
        await client.delete("run_1")
    assert err.value.code == "sandbox_unavailable"
    await http.aclose()


async def test_submit_requires_created():
    client, http = client_for(lambda r: httpx.Response(200, json=RUN))
    with pytest.raises(SandboxError, match="answered 200"):
        await client.submit(SPEC)
    await http.aclose()


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (httpx.ConnectError("refused http://x?token=secret-token"), "unreachable"),
        (httpx.ReadTimeout("slow"), "timed out"),
    ],
)
async def test_network_errors_mean_unavailable(error, text):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    client, http = client_for(handler)
    with pytest.raises(SandboxError, match=text) as err:
        await client.get("run_1")
    assert err.value.code == "sandbox_unavailable" and "secret-token" not in err.value.message
    await http.aclose()


@pytest.mark.parametrize(
    "response", [httpx.Response(200, text="<html>"), httpx.Response(200, json={"id": 1})]
)
async def test_unexpected_bodies(response):
    client, http = client_for(lambda r: response)
    with pytest.raises(SandboxError, match="unexpected"):
        await client.get("run_1")
    await http.aclose()
