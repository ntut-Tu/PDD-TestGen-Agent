"""The sandbox fix loop: run the tests, ask the model to fix failures, run again."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from pdd_agent.agents.sandbox_fix.classes import class_names
from pdd_agent.agents.sandbox_fix.prompt import build_fix_prompt, parse_fix
from pdd_agent.agents.testgen.javacheck import check_path
from pdd_agent.domain.errors import DomainError, LlmError, QuotaExhaustedError, SandboxError
from pdd_agent.domain.jobs import GeneratedFile, SandboxTry
from pdd_agent.domain.sandbox import ERROR_REASONS, SandboxFile, SandboxRun, SandboxSpec, summarize
from pdd_agent.ports.llm import LlmClient
from pdd_agent.ports.sandbox import SandboxRunner


@dataclass(frozen=True)
class ContextFile:
    """A production file shown to the model."""

    path: str
    content: str


@dataclass(frozen=True)
class FixPolicy:
    """Loop limits: tries in total, seconds between polls, seconds to wait for one run."""

    max_tries: int
    poll_interval: float
    run_timeout: float


@dataclass(frozen=True)
class FixProgress:
    """State after a try or a fix; ``files`` is the latest content of all test files."""

    tries: list[SandboxTry]
    files: list[GeneratedFile]


@dataclass(frozen=True)
class FixTask:
    """The generated tests of a pull request head to run and fix.

    ``on_progress`` is called after every try and every applied fix.
    """

    account: str
    project_id: int
    repo_id: int
    number: int
    head_sha: str
    files: Sequence[GeneratedFile]
    context: Sequence[ContextFile] = ()
    reference: str = ""
    on_progress: Callable[[FixProgress], Awaitable[None] | None] | None = field(
        default=None, compare=False
    )


@dataclass(frozen=True)
class FixOutput:
    """Final sandbox result. ``reason`` is set when ``status`` is ``fail``."""

    status: Literal["success", "fail"]
    reason: str | None
    message: str
    tries: list[SandboxTry]
    files: list[GeneratedFile]


class SandboxFixAgent:
    """Runs generated tests through a ``SandboxRunner`` and repairs them with a model.

    Model requests go through the ``LlmClient`` handed in, so its rate limit, retry and
    quota apply. The agent never edits anything but test files.
    """

    def __init__(
        self,
        runner: SandboxRunner,
        llm: LlmClient,
        policy: FixPolicy,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._runner = runner
        self._llm = llm
        self._policy = policy
        self._sleep = sleep
        self._clock = clock

    async def run(self, task: FixTask) -> FixOutput:
        """Try at most ``policy.max_tries`` times; model and sandbox failures end as ``fail``."""
        files = list(task.files)
        tries: list[SandboxTry] = []
        rejection: str | None = None
        if not any(check_path(f.path) is None for f in files):
            return self._fail("internal", "there are no valid test files to run", tries, files)
        run: SandboxRun | None = None
        for number in range(1, self._policy.max_tries + 1):
            if run is None or rejection is None:
                try:
                    run = await self._execute(task, files)
                except SandboxError as exc:
                    reason = exc.code if exc.code in ERROR_REASONS else "internal"
                    return self._fail(reason, exc.message, tries, files)
                status = run.status
                tries.append(
                    SandboxTry(
                        number=number,
                        run_id=run.id,
                        phase=status.phase,
                        reason=status.reason,
                        summary=summarize(status),
                    )
                )
                await self._notify(task, tries, files)
                if status.phase == "Succeeded":
                    return FixOutput("success", None, "all generated tests passed", tries, files)
                if status.phase == "Error":
                    reason = status.reason if status.reason in ERROR_REASONS else "internal"
                    return self._fail(reason, status.message, tries, files)
            else:
                # The files did not change, so the last run still describes them: no new run.
                tries.append(
                    SandboxTry(
                        number=number,
                        run_id=run.id,
                        phase="Rejected",
                        reason=run.status.reason,
                        summary=f"the model's fix was rejected: {rejection}",
                    )
                )
                await self._notify(task, tries, files)
            if number == self._policy.max_tries:
                message = f"the tests still failed after {number} tries"
                if rejection:
                    message += f"; last problem: {rejection}"
                return self._fail("max_tries", message, tries, files)
            try:
                answer = await self._ask_for_fix(task, run, files, number, rejection)
            except DomainError as exc:
                message = exc.message
                if exc.code == QuotaExhaustedError.default_code:
                    message += "; the tests could not be fixed"
                return self._fail("internal", message, tries, files)
            try:
                fix = parse_fix(answer)
            except LlmError as exc:
                rejection = exc.message
                continue
            rejection = None
            if fix.verdict == "production_bug":
                return self._fail("production_bug", fix.reason, tries, files)
            files = _apply(files, fix.files)
            await self._notify(task, tries, files)
        raise AssertionError("the loop ends with a result")  # pragma: no cover

    async def _ask_for_fix(
        self,
        task: FixTask,
        run: SandboxRun,
        files: list[GeneratedFile],
        number: int,
        rejection: str | None,
    ) -> str:
        pull = (
            f"project {task.project_id}, repository {task.repo_id}, pull request"
            f" #{task.number} at commit {task.head_sha}"
        )
        production = [(f.path, f.content) for f in task.context]
        prompt = build_fix_prompt(
            run, files, production, pull, number, self._policy.max_tries, task.reference, rejection
        )
        answer = await self._llm.generate(prompt)
        return answer.text

    async def _execute(self, task: FixTask, files: list[GeneratedFile]) -> SandboxRun:
        runnable = [f for f in files if check_path(f.path) is None]
        spec = SandboxSpec(
            account=task.account,
            project_id=task.project_id,
            repo_id=task.repo_id,
            head_sha=task.head_sha,
            files=[SandboxFile(path=f.path, content=f.content) for f in runnable],
            test_classes=class_names(runnable),
        )
        run = await self._runner.submit(spec)
        deadline = self._clock() + self._policy.run_timeout
        while not run.finished:
            if self._clock() >= deadline:
                await self._abandon(run.id)
                raise SandboxError("the sandbox run did not finish in time")
            await self._sleep(self._policy.poll_interval)
            run = await self._runner.get(run.id)
        return run

    async def _abandon(self, run_id: str) -> None:
        with contextlib.suppress(SandboxError):
            await self._runner.delete(run_id)

    async def _notify(self, task: FixTask, tries: list[SandboxTry], files: list[GeneratedFile]):
        if task.on_progress is None:
            return
        outcome = task.on_progress(FixProgress(list(tries), list(files)))
        if outcome is not None:
            await outcome

    @staticmethod
    def _fail(
        reason: str, message: str, tries: list[SandboxTry], files: list[GeneratedFile]
    ) -> FixOutput:
        return FixOutput("fail", reason, message, tries, files)


def _apply(files: list[GeneratedFile], changes: list[GeneratedFile]) -> list[GeneratedFile]:
    """Replace files with the same path and append new ones, keeping the order."""
    updated = {f.path: f for f in changes}
    merged = [updated.pop(f.path, f) for f in files]
    return merged + list(updated.values())
