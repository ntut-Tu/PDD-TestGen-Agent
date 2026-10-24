"""Prompt context assembly under a size budget."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field

from pdd_agent.agents.testgen.selection import Selection, package_path
from pdd_agent.ports.source import PullFile, RepoFile

TRUNCATION_MARKER = "\n// ... truncated ...\n"
MIN_USEFUL_CHARS = 200
FETCH_CONCURRENCY = 8

FileFetcher = Callable[[str], Awaitable[RepoFile | None]]


@dataclass(frozen=True)
class ContextBudget:
    """Size limits of the prompt context."""

    max_chars: int
    max_files: int
    max_file_chars: int


@dataclass(frozen=True)
class SourceFile:
    """A file included in the prompt, possibly cut to fit the budget."""

    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class ContextBundle:
    """Everything that goes into the prompt, plus a report of what was left out."""

    diff: str
    production: list[SourceFile] = field(default_factory=list)
    tests: list[SourceFile] = field(default_factory=list)
    omitted: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def included(self) -> list[str]:
        """Paths of the files included in the prompt, in prompt order."""
        return [f.path for f in (*self.production, *self.tests)]


def _cut(text: str, limit: int) -> tuple[str, bool]:
    """Cut ``text`` to at most ``limit`` characters including the truncation marker."""
    if len(text) <= limit:
        return text, False
    return text[: max(limit - len(TRUNCATION_MARKER), 0)] + TRUNCATION_MARKER, True


def build_diff(changed: Sequence[PullFile], config: ContextBudget) -> tuple[str, bool]:
    """Render the pull request diff, capped per file and at half of the total budget."""
    truncated = False
    sections: list[str] = []
    for file in changed:
        patch, cut = _cut(file.patch or "(no textual diff)", config.max_file_chars)
        truncated = truncated or cut
        header = f"--- {file.previous_path or file.path} ({file.status})\n+++ {file.path}\n"
        sections.append(header + patch)
    diff, cut = _cut("\n".join(sections), config.max_chars // 2)
    return diff, truncated or cut


def _shared_prefix(a: Sequence[str], b: Sequence[str]) -> int:
    count = 0
    for left, right in zip(a, b, strict=False):
        if left != right:
            break
        count += 1
    return count


def order_tests_by_proximity(tests: Sequence[str], anchors: Sequence[str]) -> list[str]:
    """Order test files so the ones closest (by package) to the anchor classes come first."""
    anchor_parts = [package_path(a).split("/") for a in anchors]

    def closeness(test: str) -> int:
        parts = package_path(test).split("/")
        return max((_shared_prefix(parts, a) for a in anchor_parts), default=0)

    return sorted(tests, key=lambda t: (-closeness(t), t))


async def _fetch_all(fetch: FileFetcher, paths: Sequence[str]) -> list[RepoFile | None]:
    gate = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(path: str) -> RepoFile | None:
        async with gate:
            return await fetch(path)

    return list(await asyncio.gather(*(one(p) for p in paths)))


async def assemble_context(
    fetch: FileFetcher,
    changed: Sequence[PullFile],
    selection: Selection,
    config: ContextBudget,
) -> ContextBundle:
    """Fill the prompt context by priority until a budget is exhausted.

    Priority: PR diff, changed production files, other selected production files, then
    test examples (closest package first). Files over the file-count limit are not
    fetched; later files are cut or omitted once ``maxChars`` runs out.
    """
    diff, truncated = build_diff(changed, config)
    changed_paths = {f.path for f in changed}
    first = [p for p in selection.production if p in changed_paths]
    rest = [p for p in selection.production if p not in changed_paths]
    tests = order_tests_by_proximity(selection.tests, selection.targets)
    ordered = [*first, *rest, *tests]
    candidates, omitted = ordered[: config.max_files], ordered[config.max_files :]
    fetched = await _fetch_all(fetch, candidates)

    remaining = config.max_chars - len(diff)
    production_set = set(selection.production)
    production: list[SourceFile] = []
    test_files: list[SourceFile] = []
    for path, repo_file in zip(candidates, fetched, strict=True):
        if repo_file is None or repo_file.binary or remaining < MIN_USEFUL_CHARS:
            omitted.append(path)
            continue
        content, cut_file = _cut(repo_file.content, config.max_file_chars)
        content, cut_budget = _cut(content, remaining)
        remaining -= len(content)
        source = SourceFile(path, content, cut_file or cut_budget or repo_file.truncated)
        (production if path in production_set else test_files).append(source)
    truncated = truncated or bool(omitted) or any(f.truncated for f in (*production, *test_files))
    return ContextBundle(diff, production, test_files, omitted, truncated)
