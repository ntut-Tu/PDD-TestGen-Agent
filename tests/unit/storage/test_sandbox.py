import sqlite3

from pdd_agent.domain.jobs import (
    Job,
    JobContext,
    JobResult,
    PullRef,
    SandboxOutcome,
    SandboxTry,
    Usage,
)
from pdd_agent.storage import Database, JobRepository, SandboxTryRepository
from pdd_agent.storage.migrations import MIGRATIONS


def make_job(job_id="j1", sandbox: SandboxOutcome | None = None) -> Job:
    return Job(
        id=job_id,
        status="succeeded",
        created_at="2026-10-24T10:00:00Z",
        updated_at="2026-10-24T10:00:00Z",
        account="alice",
        pull=PullRef(project_id=7, repo_id=3, number=5, head_sha="a" * 40),
        context=JobContext(production_paths=[], test_paths=[]),
        result=JobResult(files=[], model="m", usage=Usage()),
        sandbox=sandbox,
    )


def test_database_from_before_the_sandbox_still_loads_its_jobs(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path, isolation_level=None)
    for number, script in enumerate(MIGRATIONS[:2], start=1):
        old.executescript(f"BEGIN;{script}PRAGMA user_version = {number};COMMIT;")
    legacy = make_job().model_dump_json(by_alias=True, exclude={"sandbox"})
    old.execute(
        "INSERT INTO jobs (id, project_id, repo_id, number, status, created_at, data)"
        " VALUES ('j1', 7, 3, 5, 'succeeded', '2026-10-24T10:00:00Z', ?)",
        (legacy,),
    )
    old.close()

    db = Database(path)
    job = JobRepository(db).get("j1")
    assert job is not None and job.sandbox is None
    assert '"sandbox"' not in legacy
    assert db.query("PRAGMA user_version")[0][0] == len(MIGRATIONS)
    assert SandboxTryRepository(db).list_for_job("j1") == []
    db.close()


def test_tries_are_kept_per_job_and_overwritten_by_number(tmp_path):
    db = Database(tmp_path / "t.db")
    repo = SandboxTryRepository(db)
    first = SandboxTry(number=1, run_id="r1", phase="Failed", reason="tests_failed", summary="x")
    repo.save("j1", first)
    repo.save("j1", SandboxTry(number=2, run_id="r2", phase="Succeeded"))
    repo.save("j2", SandboxTry(number=1, run_id="r9", phase="Error", reason="internal"))
    repo.save("j1", first.model_copy(update={"summary": "y"}))

    tries = repo.list_for_job("j1")
    assert [(t.number, t.run_id, t.phase, t.reason, t.summary) for t in tries] == [
        (1, "r1", "Failed", "tests_failed", "y"),
        (2, "r2", "Succeeded", None, ""),
    ]
    db.close()


def test_running_sandboxes_and_sandboxed_reuse(tmp_path):
    db = Database(tmp_path / "j.db")
    jobs = JobRepository(db)
    running = make_job("running", SandboxOutcome(status="running"))
    passed = make_job("passed", SandboxOutcome(status="success"))
    flaky = make_job("flaky", SandboxOutcome(status="fail", reason="sandbox_unavailable"))
    bug = make_job("bug", SandboxOutcome(status="fail", reason="production_bug"))
    for job in (running, passed, flaky, bug, make_job("plain")):
        jobs.insert(job, reuse_key=f"key-{job.id}")

    assert [j.id for j in jobs.list_sandbox_running()] == ["running"]
    since = "2026-01-01T00:00:00Z"
    found = {
        name: jobs.find_reusable(f"key-{name}", since, sandboxed=True)
        for name in ("running", "passed", "flaky", "bug", "plain")
    }
    assert {k for k, v in found.items() if v} == {"passed", "bug"}
    assert jobs.find_reusable("key-plain", since) is not None
    assert jobs.find_reusable("key-running", since) is not None
    db.close()
