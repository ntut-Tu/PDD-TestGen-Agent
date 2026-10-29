"""The fix loop runs end to end against in-memory fakes of the sandbox and the model."""

import json

import pytest

from pdd_agent.agents.sandbox_fix import (
    ContextFile,
    FixPolicy,
    FixProgress,
    FixTask,
    SandboxFixAgent,
)
from pdd_agent.domain.errors import LlmError, QuotaExhaustedError, SandboxError
from pdd_agent.domain.jobs import GeneratedFile
from pdd_agent.domain.sandbox import (
    ClassResult,
    SandboxResult,
    SandboxRun,
    SandboxSpec,
    SandboxStatus,
)
from pdd_agent.ports.llm import LlmPrompt, LlmResponse, LlmUsage
from tests.conftest import CALC_SOURCE, GOOD_TEST, PROD, TEST

HEAD = "a" * 40
FIXED_TEST = GOOD_TEST.replace("assertEquals(3", "assertEquals(3L + 0")


def status(phase, reason=None, message="", *, output="", counts=(1, 0)) -> SandboxStatus:
    tests = [
        ClassResult(**{"class": "com.acme.CalcTest", "passed": counts[0], "failed": counts[1]})
    ]
    result = SandboxResult(compiled=True, tests=tests, output=output)
    return SandboxStatus(phase=phase, reason=reason, message=message, result=result)


SUCCEEDED = status("Succeeded")
FAILED = status("Failed", "tests_failed", "1 test failed", output="expected 3 but was 4")


class FakeRunner:
    """Each submitted run is Pending, then Running once, then the next scripted status."""

    def __init__(self, *outcomes: SandboxStatus | SandboxError) -> None:
        self.outcomes = list(outcomes)
        self.specs: list[SandboxSpec] = []
        self.deleted: list[str] = []
        self.gets = 0
        self._final: dict[str, SandboxStatus] = {}

    async def submit(self, spec):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, SandboxError):
            raise outcome
        self.specs.append(spec)
        run_id = f"run_{len(self.specs)}"
        self._final[run_id] = outcome
        return SandboxRun(id=run_id, spec=spec, status=SandboxStatusPending)

    async def get(self, run_id):
        self.gets += 1
        phase = self._final[run_id] if self.gets % 2 == 0 else SandboxStatus(phase="Running")
        return SandboxRun(id=run_id, spec=self.specs[-1], status=phase)

    async def delete(self, run_id):
        self.deleted.append(run_id)


SandboxStatusPending = SandboxStatus(phase="Pending")


class FakeLlm:
    def __init__(self, *answers: str | Exception) -> None:
        self.answers = list(answers)
        self.prompts: list[LlmPrompt] = []

    async def generate(self, prompt):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return LlmResponse(answer, LlmUsage(), "fake")


def fix_answer(content: str = FIXED_TEST, path: str = TEST) -> str:
    return json.dumps(
        {"verdict": "fix", "reason": "", "files": [{"path": path, "content": content}]}
    )


def generated(path=TEST, content=GOOD_TEST) -> GeneratedFile:
    return GeneratedFile(path=path, content=content, syntax_valid=True)


def make_agent(runner, llm, max_tries=3, sleep=None, clock=None):
    async def no_sleep(seconds):
        return None

    kwargs = {"sleep": sleep or no_sleep}
    if clock is not None:
        kwargs["clock"] = clock
    return SandboxFixAgent(runner, llm, FixPolicy(max_tries, 2.0, 60.0), **kwargs)


def task(files=None, **kw) -> FixTask:
    files = files if files is not None else [generated()]
    return FixTask(
        "alice", 7, 3, 5, HEAD, files, [ContextFile(PROD, CALC_SOURCE)], reference="job-1", **kw
    )


async def test_success_on_the_first_try():
    runner, llm = FakeRunner(SUCCEEDED), FakeLlm()
    output = await make_agent(runner, llm).run(task())

    assert (output.status, output.reason) == ("success", None)
    assert [(t.number, t.phase, t.run_id) for t in output.tries] == [(1, "Succeeded", "run_1")]
    assert llm.prompts == []
    [spec] = runner.specs
    assert (spec.account, spec.project_id, spec.repo_id, spec.head_sha) == ("alice", 7, 3, HEAD)
    assert [f.path for f in spec.files] == [TEST]
    assert spec.test_classes == ["com.acme.CalcTest"]


async def test_success_after_a_fix_sends_the_latest_files_and_a_full_prompt():
    runner, llm = FakeRunner(FAILED, SUCCEEDED), FakeLlm(fix_answer())
    progress: list[FixProgress] = []
    output = await make_agent(runner, llm).run(task(on_progress=progress.append))

    assert output.status == "success"
    assert [t.phase for t in output.tries] == ["Failed", "Succeeded"]
    assert output.tries[0].reason == "tests_failed"
    assert "1 test failed" in output.tries[0].summary and "1 passed" in output.tries[0].summary
    assert output.files[0].content == FIXED_TEST
    assert [f.content for f in runner.specs[1].files] == [FIXED_TEST]
    assert [len(p.tries) for p in progress] == [1, 1, 2]
    assert progress[1].files[0].content == FIXED_TEST

    [prompt] = llm.prompts
    assert prompt.reference == "job-1" and prompt.response_schema is not None
    for part in (
        "expected 3 but was 4",
        "tests_failed",
        "com.acme.CalcTest: 1 passed, 0 failed",
        "class CalcTest",
        "class Calc ",
        "pull request #5",
        "try 1 of 3",
    ):
        assert part in prompt.user
    assert "Never change production code" in prompt.system


async def test_async_progress_callbacks_are_awaited():
    seen: list[int] = []

    async def on_progress(progress: FixProgress) -> None:
        seen.append(len(progress.tries))

    await make_agent(FakeRunner(SUCCEEDED), FakeLlm()).run(task(on_progress=on_progress))
    assert seen == [1]


async def test_fix_may_add_files_and_keeps_untouched_ones():
    helper = "src/test/java/com/acme/Helper.java"
    helper_src = "package com.acme;\nclass Helper {}\n"
    answer = json.dumps(
        {
            "verdict": "fix",
            "reason": "",
            "files": [{"path": helper, "content": helper_src}],
        }
    )
    runner = FakeRunner(FAILED, SUCCEEDED)
    output = await make_agent(runner, FakeLlm(answer)).run(task())
    assert [f.path for f in output.files] == [TEST, helper]
    assert runner.specs[1].test_classes == ["com.acme.CalcTest", "com.acme.Helper"]


async def test_stops_after_max_tries():
    runner = FakeRunner(FAILED, FAILED, FAILED)
    llm = FakeLlm(fix_answer(), fix_answer())
    output = await make_agent(runner, llm).run(task())

    assert (output.status, output.reason) == ("fail", "max_tries")
    assert "3 tries" in output.message
    assert len(output.tries) == 3 and len(llm.prompts) == 2


async def test_production_bug_verdict_ends_the_loop_with_its_reason():
    answer = json.dumps({"verdict": "production_bug", "reason": "add()\nsubtracts instead"})
    output = await make_agent(FakeRunner(FAILED), FakeLlm(answer)).run(task())

    assert (output.status, output.reason) == ("fail", "production_bug")
    assert output.message == "add() subtracts instead"
    assert len(output.tries) == 1


@pytest.mark.parametrize(
    "reason", ["sandbox_unavailable", "unsupported_project", "source_unavailable", "internal"]
)
async def test_error_phase_fails_with_its_reason(reason):
    runner = FakeRunner(status("Error", reason, "cannot run"))
    llm = FakeLlm()
    output = await make_agent(runner, llm).run(task())
    assert (output.status, output.reason, output.message) == ("fail", reason, "cannot run")
    assert output.tries[0].phase == "Error" and llm.prompts == []


async def test_unknown_error_reason_is_internal():
    output = await make_agent(FakeRunner(status("Error", "weird", "x")), FakeLlm()).run(task())
    assert output.reason == "internal"


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (SandboxError("controller down", "sandbox_unavailable"), "sandbox_unavailable"),
        (SandboxError("bad answer"), "internal"),
        (SandboxError("odd", "whatever"), "internal"),
    ],
)
async def test_runner_errors_end_the_loop(error, reason):
    output = await make_agent(FakeRunner(error), FakeLlm()).run(task())
    assert (output.status, output.reason) == ("fail", reason)
    assert output.tries == []


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        "[]",
        json.dumps({"verdict": "maybe", "reason": "x"}),
        json.dumps({"verdict": "production_bug", "reason": " "}),
        json.dumps({"verdict": "fix", "reason": "", "files": []}),
        json.dumps({"verdict": "fix", "reason": "", "files": [{"path": TEST}]}),
        fix_answer("class CalcTest {"),
    ],
)
async def test_invalid_model_output_counts_as_a_try_and_is_fed_back(answer):
    runner = FakeRunner(FAILED, SUCCEEDED)
    llm = FakeLlm(answer, fix_answer())
    output = await make_agent(runner, llm).run(task())
    assert output.status == "success" and len(runner.specs) == 2
    assert [(t.number, t.run_id, t.phase) for t in output.tries] == [
        (1, "run_1", "Failed"),
        (2, "run_1", "Rejected"),
        (3, "run_2", "Succeeded"),
    ]
    assert output.tries[1].reason == "tests_failed"
    assert output.tries[1].summary.startswith("the model's fix was rejected: ")
    assert [f.content for f in runner.specs[1].files] == [FIXED_TEST]
    assert "previous answer was rejected" not in llm.prompts[0].user
    second = llm.prompts[1].user
    assert "Your previous answer was rejected" in second
    assert "could not be used" in second and "try 2 of 3" in second


async def test_rejected_answers_until_the_budget_ends_report_the_last_problem():
    runner = FakeRunner(FAILED)
    bad = fix_answer(CALC_SOURCE, PROD)
    output = await make_agent(runner, FakeLlm("not json", bad)).run(task())
    assert (output.status, output.reason) == ("fail", "max_tries")
    assert "3 tries" in output.message and PROD in output.message
    assert [t.phase for t in output.tries] == ["Failed", "Rejected", "Rejected"]
    assert output.files[0].content == GOOD_TEST and len(runner.specs) == 1


@pytest.mark.parametrize(
    "path", [PROD, "pom.xml", "src/test/../main/java/com/acme/Calc.java", "/src/test/java/A.java"]
)
async def test_edits_of_non_test_paths_are_rejected(path):
    runner = FakeRunner(FAILED)
    llm = FakeLlm(fix_answer(CALC_SOURCE, path))
    output = await make_agent(runner, llm, max_tries=2).run(task())
    assert (output.status, output.reason) == ("fail", "max_tries")
    assert path in output.message and len(runner.specs) == 1


async def test_quota_exhausted_mid_loop_fails_with_a_clear_message():
    quota = QuotaExhaustedError(reset_at=2000.0, now=1000.0)
    runner = FakeRunner(FAILED, SUCCEEDED)
    output = await make_agent(runner, FakeLlm(quota)).run(task())
    assert (output.status, output.reason) == ("fail", "internal")
    assert "quota" in output.message and "could not be fixed" in output.message
    assert len(output.tries) == 1


async def test_quota_error_of_the_managed_client_is_reported_the_same_way():
    quota = LlmError("quota_exhausted", "daily Gemini quota is used up")
    output = await make_agent(FakeRunner(FAILED), FakeLlm(quota)).run(task())
    assert output.reason == "internal"
    assert output.message == "daily Gemini quota is used up; the tests could not be fixed"


async def test_model_errors_end_the_loop():
    llm = FakeLlm(LlmError("rate_limited", "Gemini rate limit hit"))
    output = await make_agent(FakeRunner(FAILED), llm).run(task())
    assert (output.status, output.reason, output.message) == (
        "fail",
        "internal",
        "Gemini rate limit hit",
    )


async def test_files_outside_src_test_are_not_run_and_none_valid_fails():
    odd = generated(PROD, CALC_SOURCE)
    runner = FakeRunner(SUCCEEDED)
    output = await make_agent(runner, FakeLlm()).run(task([generated(), odd]))
    assert [f.path for f in runner.specs[0].files] == [TEST]
    assert [f.path for f in output.files] == [TEST, PROD]

    runner = FakeRunner()
    output = await make_agent(runner, FakeLlm()).run(task([odd]))
    assert (output.status, output.reason) == ("fail", "internal")
    assert "no valid test files" in output.message and runner.specs == []


async def test_waiting_polls_until_the_run_is_terminal():
    sleeps: list[float] = []

    async def sleep(seconds):
        sleeps.append(seconds)

    runner = FakeRunner(SUCCEEDED)
    await make_agent(runner, FakeLlm(), sleep=sleep).run(task())
    assert sleeps == [2.0, 2.0] and runner.gets == 2


async def test_run_timeout_deletes_the_run_and_fails():
    now = [0.0]

    async def sleep(seconds):
        now[0] += 40

    class Stuck(FakeRunner):
        async def get(self, run_id):
            return SandboxRun(id=run_id, spec=self.specs[-1], status=SandboxStatusPending)

        async def delete(self, run_id):
            await super().delete(run_id)
            raise SandboxError("already gone")

    runner = Stuck(SUCCEEDED)
    output = await make_agent(runner, FakeLlm(), sleep=sleep, clock=lambda: now[0]).run(task())
    assert (output.status, output.reason) == ("fail", "internal")
    assert "did not finish in time" in output.message
    assert runner.deleted == ["run_1"]
