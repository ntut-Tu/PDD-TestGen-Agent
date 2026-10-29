"""The test generation agent: select files, build context and prompt, ask the model, validate."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pdd_agent.agents.testgen.context import (
    ContextBudget,
    FileFetcher,
    SourceFile,
    assemble_context,
)
from pdd_agent.agents.testgen.javacheck import validate_file
from pdd_agent.agents.testgen.prompt import build_prompt, detect_stack, parse_answer
from pdd_agent.agents.testgen.selection import (
    DEFAULT_TEST_PATH,
    Selection,
    changed_java_production,
    find_build_file,
    normalize_path,
    resolve_selection,
)
from pdd_agent.domain.errors import PullChangedError, SourceError, UnsupportedLanguageError
from pdd_agent.domain.jobs import JobResult, Usage
from pdd_agent.ports.llm import LlmClient
from pdd_agent.ports.source import PullFiles, RepoFile, SourceRepository, TreeEntry


@dataclass(frozen=True)
class GenerationTask:
    """What to generate tests for.

    Empty path lists select the defaults. ``head_sha`` pins the pull request head: files
    are read at that commit and ``PullChangedError`` is raised when the head moved.
    ``reference`` is recorded with the model usage of the run.
    """

    account: str
    project_id: int
    repo_id: int
    number: int
    production_paths: Sequence[str] = ()
    test_paths: Sequence[str] = ()
    head_sha: str | None = None
    reference: str = ""


@dataclass(frozen=True)
class Plan:
    """Everything resolved from the pull request before the prompt is built."""

    pull: PullFiles
    entries: list[TreeEntry]
    build_file: str
    production_paths: list[str]
    test_paths: list[str]
    selection: Selection


@dataclass(frozen=True)
class GenerationOutput:
    """Generated test files and what went into the prompt."""

    result: JobResult
    included_files: list[str]
    omitted_files: list[str]
    truncated: bool
    production: list[SourceFile] = field(default_factory=list)


class GenerationAgent:
    """Generates JUnit tests for a pull request through a source repository and a model."""

    def __init__(self, source: SourceRepository, llm: LlmClient, budget: ContextBudget) -> None:
        self._source = source
        self._llm = llm
        self._budget = budget

    async def plan(self, task: GenerationTask) -> Plan:
        """Read the pull request and tree and resolve the selection (defaults applied).

        Raises ``SourceError``, ``UnsupportedLanguageError``, ``SelectionError`` or
        ``PullChangedError``; the model is not called.
        """
        source = self._source
        pull = await source.pull_files(task.account, task.project_id, task.repo_id, task.number)
        if task.head_sha and pull.head_sha != task.head_sha:
            raise PullChangedError
        tree = await source.tree(task.account, task.project_id, task.repo_id, pull.head_sha)
        build_file = find_build_file(tree.entries)
        if build_file is None:
            raise UnsupportedLanguageError
        production = [normalize_path(p) for p in task.production_paths] or changed_java_production(
            pull.files
        )
        tests = [normalize_path(p) for p in task.test_paths] or [DEFAULT_TEST_PATH]
        selection = resolve_selection(tree.entries, pull.files, production, tests)
        return Plan(pull, tree.entries, build_file, production, tests, selection)

    async def run(self, task: GenerationTask) -> GenerationOutput:
        """Generate the test files of ``task`` and validate each of them."""
        plan = await self.plan(task)
        fetch = self._fetcher(task, plan.pull.head_sha)
        bundle = await assemble_context(fetch, plan.pull.files, plan.selection, self._budget)
        build = await fetch(plan.build_file)
        stack = detect_stack(build.content if build else "", [f.content for f in bundle.tests])
        prompt = build_prompt(bundle, plan.selection.targets, stack, task.reference)
        answer = await self._llm.generate(prompt)
        usage = Usage(
            prompt_tokens=answer.usage.prompt_tokens, output_tokens=answer.usage.output_tokens
        )
        result = JobResult(
            files=[validate_file(f.path, f.content) for f in parse_answer(answer.text)],
            model=answer.model,
            usage=usage,
        )
        return GenerationOutput(
            result, bundle.included, bundle.omitted, bundle.truncated, bundle.production
        )

    def _fetcher(self, task: GenerationTask, ref: str) -> FileFetcher:
        async def fetch(path: str) -> RepoFile | None:
            try:
                return await self._source.file(
                    task.account, task.project_id, task.repo_id, ref, path
                )
            except SourceError as exc:
                if exc.status == 404:
                    return None
                raise

        return fetch
