"""Composition root of the service layer: builds the clients and wires the agent to them."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import httpx

from pdd_agent.agents.sandbox_fix import FixPolicy, SandboxFixAgent
from pdd_agent.agents.testgen import ContextBudget, GenerationAgent
from pdd_agent.config import Settings
from pdd_agent.integrations.git_gateway import GitGatewayClient
from pdd_agent.integrations.pdd_backend import Membership
from pdd_agent.integrations.sandbox_controller import SandboxControllerClient
from pdd_agent.llm.registry import create_client
from pdd_agent.llm.usage import ManagedLlmClient, RateLimiter
from pdd_agent.ports.llm import LlmClient
from pdd_agent.service.execution import JobExecutor
from pdd_agent.service.jobs import JobService
from pdd_agent.service.sandbox import SandboxExecutor
from pdd_agent.service.worker import JobRunner
from pdd_agent.storage import Database, JobRepository, SandboxTryRepository, UsageRepository

DB_FILE = "test-agent.db"


@dataclass(frozen=True)
class Services:
    """The long-lived collaborators of the application."""

    jobs: JobService
    executor: JobExecutor
    runner: JobRunner
    limiter: RateLimiter


@asynccontextmanager
async def open_services(settings: Settings) -> AsyncIterator[Services]:
    """Open the database and HTTP clients, start the workers and release everything on exit."""
    db = Database(Path(settings.storage.data_dir) / DB_FILE)
    backend_http = httpx.AsyncClient(
        verify=not settings.pdd.backend.insecure_tls,
        timeout=settings.pdd.backend.timeout.total_seconds(),
    )
    gateway_http = httpx.AsyncClient(timeout=30)
    llm_http = httpx.AsyncClient()
    limiter = RateLimiter(UsageRepository(db), settings.limits)
    llm = ManagedLlmClient(create_client(llm_http, settings.llm), limiter, settings.limits)
    source = GitGatewayClient(
        gateway_http, settings.git_gateway.url, settings.git_gateway.internal_token
    )
    budget = ContextBudget(
        settings.context.max_chars, settings.context.max_files, settings.context.max_file_chars
    )
    agent = GenerationAgent(source, llm, budget)
    store = JobRepository(db)
    membership = Membership(
        backend_http,
        settings.pdd.backend.url,
        settings.pdd.backend.cache_ttl.total_seconds(),
    )
    sandbox_http = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5))
    sandbox = None
    if settings.sandbox.enabled:
        sandbox = _sandbox_executor(settings, sandbox_http, llm, store, SandboxTryRepository(db))
    executor = JobExecutor(store, agent, sandbox)
    runner = JobRunner(executor, settings.jobs.concurrency)
    runner.start()
    try:
        yield Services(
            JobService(settings, store, agent, membership, limiter), executor, runner, limiter
        )
    finally:
        await runner.stop()
        for client in (backend_http, gateway_http, llm_http, sandbox_http):
            await client.aclose()
        db.close()


def _sandbox_executor(
    settings: Settings,
    http: httpx.AsyncClient,
    llm: LlmClient,
    jobs: JobRepository,
    tries: SandboxTryRepository,
) -> SandboxExecutor:
    config = settings.sandbox
    runner = SandboxControllerClient(http, config.controller_url, config.internal_token)
    policy = FixPolicy(
        config.max_tries,
        config.poll_interval.total_seconds(),
        config.run_timeout.total_seconds(),
    )
    return SandboxExecutor(jobs, tries, SandboxFixAgent(runner, llm, policy))
