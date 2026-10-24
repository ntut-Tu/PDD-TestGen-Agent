import json

import pytest

from pdd_agent.agents.testgen.context import ContextBundle, SourceFile
from pdd_agent.agents.testgen.prompt import (
    RESPONSE_SCHEMA,
    SYSTEM_INSTRUCTION,
    build_prompt,
    detect_stack,
    parse_answer,
)
from pdd_agent.domain.errors import LlmError


def test_detect_stack():
    assert detect_stack("<artifactId>junit-jupiter</artifactId>", []).junit == 5
    assert detect_stack("testImplementation 'junit:junit:4.13'", []).junit == 4
    assert detect_stack("<artifactId>junit</artifactId>", []).junit == 4
    assert detect_stack("", ["import org.junit.Test;"]).junit == 4
    assert detect_stack("junit:junit", ["import org.junit.jupiter.api.Test;"]).junit == 5
    assert detect_stack("", []).junit == 5
    assert detect_stack("mockito-core", []).mockito
    assert detect_stack("", ["import org.mockito.Mock;"]).mockito
    assert not detect_stack("", []).mockito


def test_prompt_contains_all_context_parts():
    bundle = ContextBundle(
        diff="@@ the diff",
        production=[SourceFile("src/main/java/a/A.java", "class A {}")],
        tests=[SourceFile("src/test/java/a/BTest.java", "class BTest {}")],
        truncated=True,
    )
    prompt = build_prompt(bundle, ["src/main/java/a/A.java", "x/Y.java"], detect_stack("", []))
    assert prompt.system == SYSTEM_INSTRUCTION
    assert prompt.response_schema is RESPONSE_SCHEMA
    user = prompt.user
    assert "- src/main/java/a/A.java -> src/test/java/a/ATest.java" in user
    assert "- x/Y.java\n" in user + "\n"
    for part in ("@@ the diff", "class A {}", "class BTest {}", "JUnit 5", "truncated"):
        assert part in user


def test_prompt_without_tests_and_junit4():
    bundle = ContextBundle(diff="d", production=[SourceFile("p/A.java", "c")])
    user = build_prompt(bundle, ["p/A.java"], detect_stack("junit:junit", [])).user
    assert "style examples" not in user
    assert "JUnit 4" in user
    assert "truncated" not in user


def test_schema_requires_files():
    assert RESPONSE_SCHEMA["required"] == ["files"]


def test_prompt_carries_the_usage_reference():
    prompt = build_prompt(ContextBundle(diff="d"), [], detect_stack("", []), reference="job-1")
    assert prompt.reference == "job-1"


def test_parse_answer():
    answer = json.dumps({"files": [{"path": "a", "content": "b"}]})
    assert [(f.path, f.content) for f in parse_answer(answer)] == [("a", "b")]


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        json.dumps({"files": []}),
        json.dumps({"files": [{"path": "a"}]}),
    ],
)
def test_unusable_answers_are_invalid_responses(text):
    with pytest.raises(LlmError) as err:
        parse_answer(text)
    assert err.value.code == "invalid_response"
