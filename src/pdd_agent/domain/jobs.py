"""Jobs and their parts: the persisted and published shape (camelCase JSON)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

JobStatus = Literal["queued", "running", "succeeded", "failed"]


class CamelModel(BaseModel):
    """Base model that serialises field names as camelCase."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class PullRef(CamelModel):
    """The pull request a job belongs to."""

    project_id: int
    repo_id: int
    number: int
    head_sha: str


class JobContext(CamelModel):
    """Requested paths and what actually went into the prompt."""

    production_paths: list[str]
    test_paths: list[str]
    included_files: list[str] = Field(default_factory=list)
    omitted_files: list[str] = Field(default_factory=list)
    truncated: bool = False


class Usage(CamelModel):
    """Token usage reported by the language model."""

    prompt_tokens: int = 0
    output_tokens: int = 0


class GeneratedFile(CamelModel):
    """One generated test file with the outcome of its syntax check."""

    path: str
    content: str
    syntax_valid: bool
    syntax_error: str | None = None


class JobResult(CamelModel):
    """Successful outcome of a job."""

    files: list[GeneratedFile]
    model: str
    usage: Usage
    reused: bool = False


class JobError(CamelModel):
    """Failure reason of a job."""

    code: str
    message: str


SandboxStatusName = Literal["running", "success", "fail"]


class SandboxOutcome(CamelModel):
    """Public sandbox result of a job.

    ``reason`` is set when ``status`` is ``fail``: ``max_tries``, ``production_bug``,
    ``sandbox_unavailable``, ``unsupported_project``, ``source_unavailable`` or ``internal``.
    """

    status: SandboxStatusName
    reason: str | None = None
    message: str = ""
    tries: int = 0


class SandboxTry(CamelModel):
    """The kept record of one sandbox try (not part of the public job)."""

    number: int
    run_id: str
    phase: str
    reason: str | None = None
    summary: str = ""


class Job(CamelModel):
    """A test generation job."""

    id: str
    status: JobStatus
    created_at: str
    updated_at: str
    account: str
    pull: PullRef
    context: JobContext
    result: JobResult | None = None
    error: JobError | None = None
    sandbox: SandboxOutcome | None = None


@dataclass(frozen=True)
class Principal:
    """The authenticated caller."""

    account: str
    token: str
