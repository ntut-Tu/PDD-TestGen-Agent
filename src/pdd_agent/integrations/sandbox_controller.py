"""Client of the sandbox-controller internal API; implements ``SandboxRunner``."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from pdd_agent.domain.errors import SandboxError
from pdd_agent.domain.sandbox import SandboxRun, SandboxSpec


class SandboxControllerClient:
    """Talks to ``/internal/v1/runs`` of the sandbox-controller.

    The token is sent only in the ``X-Internal-Token`` header; error messages never contain
    it, the request body (test sources) or the response body.
    """

    def __init__(self, client: httpx.AsyncClient, base_url: str, internal_token: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/") + "/internal/v1/runs"
        self._headers = {"X-Internal-Token": internal_token}

    async def submit(self, spec: SandboxSpec) -> SandboxRun:
        """Create a run; the controller answers ``201`` with the run."""
        body = {"spec": spec.model_dump(by_alias=True)}
        response = await self._send("POST", self._base, json=body)
        return self._run(response, 201)

    async def get(self, run_id: str) -> SandboxRun:
        """Return a run by id."""
        return self._run(await self._send("GET", f"{self._base}/{run_id}"), 200)

    async def delete(self, run_id: str) -> None:
        """Request deletion of a run (``202``); an unknown run is already gone."""
        response = await self._send("DELETE", f"{self._base}/{run_id}")
        if response.status_code not in (202, 404):
            raise _status_error(response.status_code)

    async def _send(
        self, method: str, url: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        try:
            return await self._client.request(method, url, headers=self._headers, json=json)
        except httpx.TimeoutException as exc:
            raise SandboxError("sandbox-controller timed out", "sandbox_unavailable") from exc
        except httpx.HTTPError as exc:
            raise SandboxError("sandbox-controller is unreachable", "sandbox_unavailable") from exc

    @staticmethod
    def _run(response: httpx.Response, expected: int) -> SandboxRun:
        if response.status_code != expected:
            raise _status_error(response.status_code)
        try:
            return SandboxRun.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise SandboxError("unexpected sandbox-controller response") from exc


def _status_error(status: int) -> SandboxError:
    code = "sandbox_unavailable" if status in (502, 503, 504) else "internal"
    return SandboxError(f"sandbox-controller answered {status}", code)
