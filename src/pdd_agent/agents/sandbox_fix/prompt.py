"""Fix prompt and the structured answer of the model for the sandbox fix loop."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pdd_agent.agents.testgen.javacheck import check_path, validate_file
from pdd_agent.domain.errors import LlmError
from pdd_agent.domain.jobs import GeneratedFile
from pdd_agent.domain.sandbox import SandboxRun
from pdd_agent.ports.llm import LlmPrompt

OUTPUT_HEAD_CHARS = 3000
OUTPUT_TAIL_CHARS = 9000
CONTEXT_FILE_CHARS = 20000
OMITTED_MARKER = "\n... output omitted ...\n"
REASON_CHARS = 500

SYSTEM_INSTRUCTION = """\
You are a senior Java engineer who fixes failing JUnit tests.
You get the build and test output of a sandbox run of generated tests, the current test files and
the production code under test.

Rules:
- Fix the tests only. Never change production code and never output production files.
- Answer with verdict "fix" and the complete new content of every test file you change or add.
  Paths must be .java files under src/test/. Files you do not list stay as they are.
- Answer with verdict "production_bug" when the failure is caused by a defect in the production
  code and not by a mistake in the tests; put a one-sentence reason for the developer in "reason".
  Do not list files then. Choose it only when the test expectation is clearly right.
- Fix compile errors, wrong expectations and wrong test setup. Do not delete assertions just to
  make a test pass and do not skip tests.
- Use only the test libraries the tests already use. Prefer plain Java 8 compatible syntax.
- Answer only with JSON of the form
  {"verdict": "fix" | "production_bug", "reason": "...",
   "files": [{"path": "...", "content": "..."}]}.
"""

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "verdict": {"type": "STRING", "enum": ["fix", "production_bug"]},
        "reason": {"type": "STRING"},
        "files": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"path": {"type": "STRING"}, "content": {"type": "STRING"}},
                "required": ["path", "content"],
            },
        },
    },
    "required": ["verdict", "reason"],
}


@dataclass(frozen=True)
class Fix:
    """The model's answer: new files, or the verdict that production code is at fault."""

    verdict: Literal["fix", "production_bug"]
    reason: str
    files: list[GeneratedFile]


def shorten_output(output: str) -> str:
    """Keep the start (first errors) and the end (summary) of a long build output."""
    if len(output) <= OUTPUT_HEAD_CHARS + OUTPUT_TAIL_CHARS:
        return output
    return output[:OUTPUT_HEAD_CHARS] + OMITTED_MARKER + output[-OUTPUT_TAIL_CHARS:]


def _java(path: str, content: str, limit: int | None = None) -> str:
    if limit is not None and len(content) > limit:
        content = content[:limit] + "\n// ... truncated ...\n"
    return f"### {path}\n```java\n{content}\n```"


def _counts(run: SandboxRun) -> str:
    result = run.status.result
    if result is None or not result.tests:
        return "(no test results: the tests did not run)"
    return "\n".join(
        f"- {t.class_name}: {t.passed} passed, {t.failed} failed, {t.errors} errors,"
        f" {t.skipped} skipped"
        for t in result.tests
    )


def build_fix_prompt(
    run: SandboxRun,
    files: Sequence[GeneratedFile],
    production: Sequence[tuple[str, str]],
    pull: str,
    try_number: int,
    max_tries: int,
    reference: str = "",
    rejection: str | None = None,
) -> LlmPrompt:
    """Describe the failed ``run`` to the model.

    ``rejection`` is why the answer to the previous try was unusable, if it was.

    ``production`` holds (path, content) of the production context, ``pull`` identifies the
    pull request in one line.
    """
    status = run.status
    result = status.result
    parts = [
        f"## Pull request\n{pull}",
        f"## Failed try {try_number} of {max_tries}\n"
        f"Reason: {status.reason or 'unknown'}\n{status.message}".rstrip(),
        f"## Test results per class\n{_counts(run)}",
        "## Build and test output\n```\n"
        + shorten_output(result.output if result else "")
        + "\n```",
        "## Current test files\n" + "\n\n".join(_java(f.path, f.content) for f in files),
        "## Production code (do not change)\n"
        + "\n\n".join(_java(p, c, CONTEXT_FILE_CHARS) for p, c in production),
    ]
    if rejection:
        parts.insert(
            1,
            f"## Your previous answer was rejected\nIt was rejected because: {rejection}\n"
            "Answer again, following the rules and the JSON format exactly.",
        )
    return LlmPrompt(
        system=SYSTEM_INSTRUCTION,
        user="\n\n".join(parts),
        response_schema=RESPONSE_SCHEMA,
        reference=reference,
    )


def _invalid(detail: str) -> LlmError:
    return LlmError("invalid_response", f"the model's fix could not be used: {detail}")


def parse_fix(text: str) -> Fix:
    """Parse and validate the model answer; raises ``LlmError`` (``invalid_response``).

    Every file must be a Java file under ``src/test/`` and must parse; anything else is
    rejected so production code can never be edited.
    """
    try:
        data = json.loads(text)
        verdict = data["verdict"]
        reason = " ".join(str(data.get("reason", "")).split())[:REASON_CHARS]
        items = data.get("files") or []
        raw = [(str(i["path"]), str(i["content"])) for i in items]
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise _invalid("not the expected JSON") from exc
    if verdict == "production_bug":
        if not reason:
            raise _invalid("a production bug verdict needs a reason")
        return Fix("production_bug", reason, [])
    if verdict != "fix":
        raise _invalid(f"unknown verdict {verdict!r}")
    if not raw:
        raise _invalid("the fix contains no files")
    files: list[GeneratedFile] = []
    for path, content in raw:
        if (problem := check_path(path)) is not None:
            raise _invalid(f"{path}: {problem}")
        file = validate_file(path, content)
        if not file.syntax_valid:
            raise _invalid(f"{path}: {file.syntax_error}")
        files.append(file)
    return Fix("fix", reason, files)
