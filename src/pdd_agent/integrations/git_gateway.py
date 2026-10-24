"""Client of the git-gateway internal API; implements ``SourceRepository``."""

from __future__ import annotations

import httpx
from pydantic import ValidationError

from pdd_agent.domain.errors import SourceError
from pdd_agent.domain.jobs import CamelModel
from pdd_agent.ports.source import PullFiles, RepoFile, Tree


class GitGatewayClient:
    """Reads pull request data, trees and files on behalf of a PDD account."""

    def __init__(self, client: httpx.AsyncClient, base_url: str, internal_token: str) -> None:
        self._client = client
        self._base = base_url.rstrip("/") + "/internal/v1"
        self._headers = {"X-Internal-Token": internal_token}

    async def pull_files(
        self, account: str, project_id: int, repo_id: int, number: int
    ) -> PullFiles:
        """Return the changed files (with diffs) and head SHA of a pull request."""
        url = f"{self._base}/projects/{project_id}/repos/{repo_id}/pulls/{number}/files"
        return await self._get(url, {"account": account}, PullFiles)

    async def tree(self, account: str, project_id: int, repo_id: int, ref: str) -> Tree:
        """Return the repository tree at ``ref``."""
        url = f"{self._base}/projects/{project_id}/repos/{repo_id}/tree"
        return await self._get(url, {"account": account, "ref": ref}, Tree)

    async def file(
        self, account: str, project_id: int, repo_id: int, ref: str, path: str
    ) -> RepoFile:
        """Return one file at ``ref``."""
        url = f"{self._base}/projects/{project_id}/repos/{repo_id}/files"
        return await self._get(url, {"account": account, "ref": ref, "path": path}, RepoFile)

    async def _get[M: CamelModel](self, url: str, params: dict[str, str], model: type[M]) -> M:
        try:
            response = await self._client.get(url, params=params, headers=self._headers)
        except httpx.HTTPError as exc:
            raise SourceError("git-gateway is unreachable") from exc
        if response.status_code != 200:
            raise SourceError(
                f"git-gateway answered {response.status_code}", status=response.status_code
            )
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise SourceError("unexpected git-gateway response") from exc
