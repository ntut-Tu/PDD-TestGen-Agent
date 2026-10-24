"""Client of the PDD backend: project membership of a login token."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

import httpx

from pdd_agent.domain.errors import ForbiddenError, UnauthorizedError, UpstreamError
from pdd_agent.domain.jobs import Principal


class Membership:
    """Checks project membership through the PDD backend ``GET /project/``."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._url = base_url.rstrip("/") + "/project/"
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[str, tuple[float, frozenset[int]]] = {}

    async def project_ids(self, principal: Principal) -> frozenset[int]:
        """Return the ids of the projects the caller belongs to (cached per token)."""
        key = hashlib.sha256(principal.token.encode()).hexdigest()
        now = self._clock()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        ids = await self._fetch(principal.token)
        self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
        self._cache[key] = (now + self._ttl, ids)
        return ids

    async def ensure_member(self, principal: Principal, project_id: int) -> None:
        """Raise ``ForbiddenError`` unless the caller belongs to the project."""
        if project_id not in await self.project_ids(principal):
            raise ForbiddenError("not a member of this project")

    async def _fetch(self, token: str) -> frozenset[int]:
        try:
            response = await self._client.get(
                self._url, headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise UpstreamError("PDD backend is unreachable") from exc
        if response.status_code in (401, 403):
            raise UnauthorizedError("PDD backend rejected the token")
        if response.status_code != 200:
            raise UpstreamError(f"PDD backend answered {response.status_code}")
        try:
            return frozenset(int(p["id"]) for p in response.json())
        except (ValueError, TypeError, KeyError) as exc:
            raise UpstreamError("unexpected PDD backend response") from exc
