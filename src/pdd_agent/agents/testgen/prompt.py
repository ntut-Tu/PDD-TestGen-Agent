"""Prompt construction and the structured output format for test generation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass

from pdd_agent.agents.testgen.context import ContextBundle, SourceFile
from pdd_agent.agents.testgen.selection import expected_test_path
from pdd_agent.domain.errors import LlmError
from pdd_agent.ports.llm import LlmPrompt

SYSTEM_INSTRUCTION = """\
You are a senior Java engineer who writes unit tests.
Write JUnit tests for the classes changed by a pull request.

Rules:
- Cover the behaviour introduced or changed by the pull request diff, including edge cases
  and failure paths.
- Create one test class per target class, in the package of the target class, named <ClassName>Test.
  Place it under src/test/java, using the suggested path when one is given.
- Use only the test libraries stated in the request. Do not add dependencies.
- Follow the style of the existing tests that are provided (naming, assertions, structure).
- Never modify production code and never output production files.
- Every file must be complete, self-contained and compilable: package declaration, imports,
  no placeholders.
- Prefer plain Java 8 compatible syntax: no records, text blocks, switch expressions or
  pattern matching.
- Answer only with JSON of the form {"files": [{"path": "...", "content": "..."}]}.
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "files": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"path": {"type": "STRING"}, "content": {"type": "STRING"}},
                "required": ["path", "content"],
            },
        }
    },
    "required": ["files"],
}


@dataclass(frozen=True)
class JunitStack:
    """Test libraries detected in the repository."""

    junit: int = 5
    mockito: bool = False

    def describe(self) -> str:
        """Describe the stack for the prompt."""
        junit = (
            "JUnit 5 (org.junit.jupiter.api.Test)"
            if self.junit == 5
            else "JUnit 4 (org.junit.Test, org.junit.Assert)"
        )
        mockito = "Mockito is available" if self.mockito else "Mockito is not available"
        return f"{junit}. {mockito}."


@dataclass(frozen=True)
class RawFile:
    """A file as returned by the model, before validation."""

    path: str
    content: str


def detect_stack(build_text: str, test_sources: Sequence[str]) -> JunitStack:
    """Detect the JUnit generation and Mockito from the build file and existing tests."""
    build = build_text.lower()
    sources = "\n".join(test_sources)
    jupiter = "junit-jupiter" in build or "junit-bom" in build or "org.junit.jupiter" in sources
    junit4 = "junit:junit" in build or "<artifactId>junit</artifactId>" in build_text
    junit4 = junit4 or "org.junit.Test" in sources
    junit = 4 if junit4 and not jupiter else 5
    mockito = "mockito" in build or "org.mockito" in sources
    return JunitStack(junit=junit, mockito=mockito)


def _file_section(file: SourceFile) -> str:
    return f"### {file.path}\n```java\n{file.content}\n```"


def build_prompt(
    bundle: ContextBundle, targets: Sequence[str], stack: JunitStack, reference: str = ""
) -> LlmPrompt:
    """Assemble the request from the context bundle; ``reference`` is recorded with its usage."""
    target_lines = []
    for target in targets:
        suggestion = expected_test_path(target)
        target_lines.append(f"- {target}" + (f" -> {suggestion}" if suggestion else ""))
    parts = [
        "## Target classes (write tests for these)\n" + "\n".join(target_lines),
        f"## Test stack\n{stack.describe()}",
        f"## Pull request diff\n```diff\n{bundle.diff}\n```",
        "## Production code\n" + "\n\n".join(_file_section(f) for f in bundle.production),
    ]
    if bundle.tests:
        examples = "\n\n".join(_file_section(f) for f in bundle.tests)
        parts.append("## Existing tests (style examples)\n" + examples)
    if bundle.truncated:
        parts.append("Note: the context was truncated to fit the size budget.")
    return LlmPrompt(
        system=SYSTEM_INSTRUCTION,
        user="\n\n".join(parts),
        response_schema=RESPONSE_SCHEMA,
        reference=reference,
    )


def parse_answer(text: str) -> list[RawFile]:
    """Parse the model answer ``{"files": [{"path", "content"}]}`` or raise ``LlmError``."""
    try:
        items = json.loads(text)["files"]
        files = [RawFile(path=str(i["path"]), content=str(i["content"])) for i in items]
    except (KeyError, TypeError, ValueError) as exc:
        raise LlmError("invalid_response", "Gemini answer is not the expected JSON") from exc
    if not files:
        raise LlmError("invalid_response", "Gemini returned no test files")
    return files
