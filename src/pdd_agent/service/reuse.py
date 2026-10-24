"""Result reuse: identical requests share the output of the first successful job."""

from __future__ import annotations

import hashlib
import json

from pdd_agent.config import LlmConfig
from pdd_agent.domain.jobs import Job, Usage


def reuse_key(job: Job, llm: LlmConfig) -> str:
    """Fingerprint of everything that determines the generated output."""
    pull = job.pull
    parts = [
        pull.project_id,
        pull.repo_id,
        pull.head_sha,
        sorted(job.context.production_paths),
        sorted(job.context.test_paths),
        llm.provider,
        llm.model,
    ]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def reused(job: Job, previous: Job) -> Job:
    """Complete ``job`` with the result of ``previous`` without calling the model."""
    assert previous.result is not None
    result = previous.result.model_copy(update={"reused": True, "usage": Usage()})
    return job.model_copy(
        update={"status": "succeeded", "result": result, "context": previous.context}
    )
