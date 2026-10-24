"""Port of the source repository an agent reads pull requests from."""

from __future__ import annotations

from typing import Protocol

from pdd_agent.domain.jobs import CamelModel


class PullFile(CamelModel):
    """A file changed by a pull request."""

    path: str
    previous_path: str | None = None
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    patch: str | None = None


class PullFiles(CamelModel):
    """Changed files of a pull request together with its head commit."""

    head_sha: str
    base_sha: str | None = None
    files: list[PullFile]
    truncated: bool = False


class TreeEntry(CamelModel):
    """One entry of a repository tree."""

    path: str
    type: str
    size: int | None = None


class Tree(CamelModel):
    """Repository tree at a ref."""

    ref: str | None = None
    entries: list[TreeEntry]
    truncated: bool = False


class RepoFile(CamelModel):
    """A file read at a ref."""

    path: str
    content: str = ""
    binary: bool = False
    truncated: bool = False


class SourceRepository(Protocol):
    """Reads pull request data, trees and files on behalf of an account.

    Every method raises ``SourceError`` when the repository cannot serve the request.
    """

    async def pull_files(
        self, account: str, project_id: int, repo_id: int, number: int
    ) -> PullFiles:
        """Return the changed files (with diffs) and head SHA of a pull request."""
        ...

    async def tree(self, account: str, project_id: int, repo_id: int, ref: str) -> Tree:
        """Return the repository tree at ``ref``."""
        ...

    async def file(
        self, account: str, project_id: int, repo_id: int, ref: str, path: str
    ) -> RepoFile:
        """Return one file at ``ref``."""
        ...
