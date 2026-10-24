"""Shared fixtures: settings, JWTs and fake upstream services (git-gateway, backend, Gemini)."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt
import pytest
import respx
from fastapi import FastAPI

from pdd_agent.api.app import create_app
from pdd_agent.config import Settings, load_settings

SECRET = "test-secret-0123456789-abcdefghij"
GATEWAY = "http://gateway.test"
BACKEND = "https://backend.test"
GEMINI = "https://gemini.test"
PROD = "src/main/java/com/acme/Calc.java"
TEST = "src/test/java/com/acme/CalcTest.java"

CALC_SOURCE = (
    "package com.acme;\npublic class Calc {\n  public int add(int a, int b) { return a + b; }\n}\n"
)
GOOD_TEST = (
    "package com.acme;\nimport org.junit.jupiter.api.Test;\n"
    "import static org.junit.jupiter.api.Assertions.assertEquals;\n"
    "class CalcTest {\n  @Test void adds() { assertEquals(3, new Calc().add(1, 2)); }\n}\n"
)


def make_token(account: str = "alice", *, exp_in: int = 600, secret: str = SECRET, **extra) -> str:
    """Create a PDD login token the way the legacy backend does."""
    key = base64.urlsafe_b64encode(secret.encode())
    claims = {"sub": account, "oauth": "gho_x", "exp": int(time.time()) + exp_in, **extra}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="HS256")


def auth(account: str = "alice") -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(account)}"}


def gemini_answer(files: list[dict], prompt: int = 100, output: int = 50) -> dict:
    text = json.dumps({"files": files})
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": output},
    }


class Upstreams:
    """Handles of the mocked upstream routes."""

    def __init__(self, router: respx.MockRouter) -> None:
        self.router = router
        self.tree_entries = [
            {"path": "pom.xml", "type": "blob"},
            {"path": PROD, "type": "blob"},
            {"path": "src/main/java/com/acme/Other.java", "type": "blob"},
            {"path": TEST, "type": "blob"},
        ]
        self.files = {
            "pom.xml": "junit-jupiter mockito-core",
            PROD: CALC_SOURCE,
            "src/main/java/com/acme/Other.java": "package com.acme; class Other {}",
            TEST: GOOD_TEST,
        }
        self.pull_files = [
            {"path": PROD, "status": "modified", "additions": 1, "deletions": 0, "patch": "@@ +1"}
        ]
        self.head = "a" * 40
        self.projects = [{"id": 7, "name": "p"}]
        self.gemini_response = httpx.Response(
            200, json=gemini_answer([{"path": TEST, "content": GOOD_TEST}])
        )
        self.gemini = router.post(url__regex=rf"{GEMINI}/v1beta/models/.*:generateContent").mock(
            side_effect=lambda request: self.gemini_response
        )
        self.backend = router.get(f"{BACKEND}/project/").mock(
            side_effect=lambda request: httpx.Response(200, json=self.projects)
        )
        self.pull_route = router.get(
            url__regex=rf"{GATEWAY}/internal/v1/projects/7/repos/3/pulls/5/files"
        ).mock(
            side_effect=lambda request: httpx.Response(
                200, json={"headSha": self.head, "files": self.pull_files}
            )
        )
        self.tree_route = router.get(
            url__regex=rf"{GATEWAY}/internal/v1/projects/7/repos/3/tree"
        ).mock(
            side_effect=lambda request: httpx.Response(
                200, json={"ref": self.head, "entries": self.tree_entries}
            )
        )
        router.get(url__regex=rf"{GATEWAY}/internal/v1/projects/7/repos/3/files").mock(
            side_effect=self._file
        )

    def _file(self, request: httpx.Request) -> httpx.Response:
        path = request.url.params["path"]
        if path not in self.files:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "x"}})
        return httpx.Response(200, json={"path": path, "content": self.files[path]})


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    config = tmp_path / "values.yaml"
    config.write_text(
        f"""
pdd: {{jwtSecret: {SECRET}, backend: {{url: "{BACKEND}"}}}}
gitGateway: {{url: "{GATEWAY}", internalToken: internal}}
llm: {{apiKey: key, baseURL: "{GEMINI}"}}
storage: {{dataDir: {tmp_path / "data"}}}
"""
    )
    return load_settings([config])


@pytest.fixture
def upstreams() -> Iterator[Upstreams]:
    with respx.mock(assert_all_called=False) as router:
        yield Upstreams(router)


@dataclass
class Api:
    """An HTTP client bound to a running application."""

    http: httpx.AsyncClient
    app: FastAPI

    async def create_job(self, body: dict | None = None, **kwargs) -> httpx.Response:
        return await self.http.post(
            "/api/v1/projects/7/repos/3/pulls/5/generations",
            json=body or {},
            headers=kwargs.get("headers") or auth(),
        )

    async def finished(self, job_id: str, headers: dict[str, str] | None = None) -> dict:
        """Wait for every queued job, then return the job."""
        await self.app.state.container.services.runner.idle()
        response = await self.http.get(f"/api/v1/generations/{job_id}", headers=headers or auth())
        return response.json()["job"]


@asynccontextmanager
async def running_api(settings: Settings) -> AsyncIterator[Api]:
    """Start the application (lifespan included) and yield a client for it."""
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://agent.test") as http:
            yield Api(http, app)


@pytest.fixture
async def api(settings: Settings, upstreams: Upstreams) -> AsyncIterator[Api]:
    async with running_api(settings) as running:
        yield running
