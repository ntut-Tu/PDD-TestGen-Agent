from pdd_agent.agents.sandbox_fix import FixTask
from pdd_agent.domain.jobs import Job, JobContext, JobResult, PullRef, Usage
from pdd_agent.service.sandbox import SandboxExecutor
from pdd_agent.storage import Database, JobRepository, SandboxTryRepository


class ExplodingAgent:
    async def run(self, task: FixTask):
        raise RuntimeError("boom")


async def test_unexpected_failure_of_the_loop_is_recorded_as_internal(tmp_path):
    db = Database(tmp_path / "t.db")
    jobs = JobRepository(db)
    job = Job(
        id="j",
        status="succeeded",
        created_at="2026-10-24T10:00:00Z",
        updated_at="2026-10-24T10:00:00Z",
        account="alice",
        pull=PullRef(project_id=7, repo_id=3, number=5, head_sha="a" * 40),
        context=JobContext(production_paths=[], test_paths=[]),
        result=JobResult(files=[], model="m", usage=Usage()),
    )
    jobs.insert(job)
    executor = SandboxExecutor(jobs, SandboxTryRepository(db), ExplodingAgent())  # type: ignore[arg-type]

    await executor.run(job, [])
    saved = jobs.get("j")
    db.close()

    assert saved is not None and saved.status == "succeeded"
    assert saved.sandbox is not None
    assert (saved.sandbox.status, saved.sandbox.reason) == ("fail", "internal")
    assert "see the agent log" in saved.sandbox.message
