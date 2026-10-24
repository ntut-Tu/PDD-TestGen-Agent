"""The agent runs end to end against in-memory fakes: no HTTP, no database."""

import json

import pytest

from pdd_agent.agents.testgen import ContextBudget, GenerationAgent, GenerationTask
from pdd_agent.domain.errors import (
    LlmError,
    PullChangedError,
    SelectionError,
    SourceError,
    UnsupportedLanguageError,
)
from pdd_agent.ports.llm import LlmPrompt, LlmResponse, LlmUsage
from pdd_agent.ports.source import PullFile, PullFiles, RepoFile, Tree, TreeEntry
from tests.conftest import CALC_SOURCE, GOOD_TEST, PROD, TEST

HEAD = "a" * 40
BUDGET = ContextBudget(max_chars=100000, max_files=20, max_file_chars=10000)


class FakeSource:
    """A one-pull-request repository held in memory."""

    def __init__(self, files: dict[str, str] | None = None, head: str = HEAD) -> None:
        self.files = {"pom.xml": "junit-jupiter", PROD: CALC_SOURCE, TEST: GOOD_TEST}
        self.files.update(files or {})
        self.head = head
        self.requested: list[tuple[str, str]] = []

    async def pull_files(self, account, project_id, repo_id, number) -> PullFiles:
        changed = PullFile(path=PROD, status="modified", patch="@@ +1")
        return PullFiles(head_sha=self.head, files=[changed])

    async def tree(self, account, project_id, repo_id, ref) -> Tree:
        return Tree(entries=[TreeEntry(path=p, type="blob") for p in self.files])

    async def file(self, account, project_id, repo_id, ref, path) -> RepoFile:
        self.requested.append((ref, path))
        if path not in self.files:
            raise SourceError("missing", status=404)
        return RepoFile(path=path, content=self.files[path])


class FakeLlm:
    """Answers with a scripted text and records the prompts it receives."""

    def __init__(self, text: str | None = None) -> None:
        files = [{"path": TEST, "content": GOOD_TEST}]
        self.text = text if text is not None else json.dumps({"files": files})
        self.prompts: list[LlmPrompt] = []

    async def generate(self, prompt: LlmPrompt) -> LlmResponse:
        self.prompts.append(prompt)
        return LlmResponse(self.text, LlmUsage(prompt_tokens=10, output_tokens=5), "fake-model")


def task(**kw) -> GenerationTask:
    return GenerationTask("alice", 7, 3, 5, head_sha=HEAD, reference="job-1", **kw)


async def test_generates_validated_tests_for_the_changed_files():
    source, llm = FakeSource(), FakeLlm()
    output = await GenerationAgent(source, llm, BUDGET).run(task())

    [file] = output.result.files
    assert (file.path, file.syntax_valid) == (TEST, True)
    assert output.result.model == "fake-model"
    assert (output.result.usage.prompt_tokens, output.result.usage.output_tokens) == (10, 5)
    assert output.included_files == [PROD, TEST]
    assert output.omitted_files == [] and output.truncated is False
    [prompt] = llm.prompts
    assert prompt.reference == "job-1"
    assert prompt.response_schema is not None
    for part in ("@@ +1", "class Calc", "class CalcTest", "JUnit 5"):
        assert part in prompt.user
    assert {ref for ref, _ in source.requested} == {HEAD}


async def test_invalid_generated_java_is_reported_not_raised():
    bad = json.dumps({"files": [{"path": TEST, "content": "class CalcTest {"}]})
    output = await GenerationAgent(FakeSource(), FakeLlm(bad), BUDGET).run(task())
    [file] = output.result.files
    assert file.syntax_valid is False and file.syntax_error


async def test_plan_resolves_defaults_without_calling_the_model():
    llm = FakeLlm()
    plan = await GenerationAgent(FakeSource(), llm, BUDGET).plan(task())
    assert plan.production_paths == [PROD]
    assert plan.test_paths == ["src/test/java"]
    assert plan.build_file == "pom.xml"
    assert llm.prompts == []


async def test_failures_are_domain_errors():
    agent = GenerationAgent(FakeSource(head="b" * 40), FakeLlm(), BUDGET)
    with pytest.raises(PullChangedError):
        await agent.run(task())

    agent = GenerationAgent(FakeSource({"pom.xml": "x"}), FakeLlm(), BUDGET)
    del agent._source.files["pom.xml"]
    with pytest.raises(UnsupportedLanguageError):
        await agent.run(task())

    with pytest.raises(SelectionError):
        await GenerationAgent(FakeSource(), FakeLlm(), BUDGET).run(
            GenerationTask("alice", 7, 3, 5, production_paths=["nope/A.java"])
        )

    with pytest.raises(LlmError) as err:
        await GenerationAgent(FakeSource(), FakeLlm("not json"), BUDGET).run(task())
    assert err.value.code == "invalid_response"


async def test_source_errors_other_than_missing_files_propagate():
    class Broken(FakeSource):
        async def file(self, *args):
            raise SourceError("down", status=500)

    with pytest.raises(SourceError, match="down"):
        await GenerationAgent(Broken(), FakeLlm(), BUDGET).run(task())
