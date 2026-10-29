"""Typed configuration schema: dataclasses with their YAML keys and value checks."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import MISSING, dataclass, field
from datetime import timedelta
from typing import Any

_LOG_LEVELS = ("debug", "info", "warn", "error")
# Keep in sync with the registry in pdd_agent.llm.registry (a test enforces it).
LLM_PROVIDERS = ("google_genai",)


def _opt(
    default: Any = MISSING,
    *,
    key: str | None = None,
    required: bool = False,
    check: Callable[[Any], str | None] | None = None,
) -> Any:
    return field(default=default, metadata={"key": key, "required": required, "check": check})


def _positive(value: Any) -> str | None:
    if isinstance(value, timedelta):
        return None if value.total_seconds() > 0 else "must be greater than zero"
    return None if value > 0 else "must be greater than zero"


def _one_of(*allowed: str) -> Callable[[Any], str | None]:
    return lambda v: None if v in allowed else f"must be one of {', '.join(allowed)}"


def _url(value: str) -> str | None:
    return None if re.match(r"^https?://[^\s/]+", value) else "must be an http(s) URL"


def _optional_url(value: str) -> str | None:
    return None if value == "" else _url(value)


def _listen_addr(value: str) -> str | None:
    _, sep, port = value.rpartition(":")
    ok = bool(sep) and port.isdigit() and 0 < int(port) < 65536
    return None if ok else "must be host:port, e.g. 0.0.0.0:8000"


@dataclass(frozen=True)
class ServerConfig:
    """HTTP server settings."""

    listen_addr: str = _opt("0.0.0.0:8000", key="listenAddr", check=_listen_addr)
    log_level: str = _opt("info", key="logLevel", check=_one_of(*_LOG_LEVELS))


@dataclass(frozen=True)
class BackendConfig:
    """Connection to the PDD backend."""

    url: str = _opt("https://pdd-backend:5005", check=_url)
    insecure_tls: bool = _opt(False, key="insecureTLS")
    timeout: timedelta = _opt(timedelta(seconds=10), check=_positive)
    cache_ttl: timedelta = _opt(timedelta(seconds=30), key="cacheTTL")


@dataclass(frozen=True)
class PddConfig:
    """Settings shared with the PDD backend."""

    jwt_secret: str = _opt("", key="jwtSecret", required=True)
    backend: BackendConfig = field(default_factory=BackendConfig)


@dataclass(frozen=True)
class GitGatewayConfig:
    """Connection to the git-gateway internal API."""

    url: str = _opt("http://pdd-git-gateway:8080", check=_url)
    internal_token: str = _opt("", key="internalToken", required=True)


@dataclass(frozen=True)
class LlmConfig:
    """Settings of the language model provider; an empty ``baseURL`` selects its official URL."""

    provider: str = _opt("google_genai", check=_one_of(*LLM_PROVIDERS))
    model: str = _opt("gemini-3.8-flash", required=True)
    api_key: str = _opt("", key="apiKey", required=True)
    base_url: str = _opt("", key="baseURL", check=_optional_url)
    request_timeout: timedelta = _opt(timedelta(seconds=120), key="requestTimeout", check=_positive)


@dataclass(frozen=True)
class LimitsConfig:
    """Gemini usage control (rate limit, retry, result reuse)."""

    requests_per_minute: int = _opt(8, key="requestsPerMinute", check=_positive)
    requests_per_day: int = _opt(200, key="requestsPerDay", check=_positive)
    max_attempts: int = _opt(3, key="maxAttempts", check=_positive)
    base_delay: timedelta = _opt(timedelta(seconds=1), key="baseDelay", check=_positive)
    max_delay: timedelta = _opt(timedelta(seconds=30), key="maxDelay", check=_positive)
    max_retry_after: timedelta = _opt(timedelta(seconds=60), key="maxRetryAfter", check=_positive)
    reuse_window: timedelta = _opt(timedelta(hours=24), key="reuseWindow", check=_positive)


@dataclass(frozen=True)
class ContextConfig:
    """Prompt context budget."""

    max_chars: int = _opt(200000, key="maxChars", check=_positive)
    max_files: int = _opt(40, key="maxFiles", check=_positive)
    max_file_chars: int = _opt(40000, key="maxFileChars", check=_positive)


@dataclass(frozen=True)
class JobsConfig:
    """Background job execution."""

    concurrency: int = _opt(2, check=_positive)


@dataclass(frozen=True)
class StorageConfig:
    """Persistent storage."""

    data_dir: str = _opt("/data", key="dataDir", required=True)


@dataclass(frozen=True)
class SandboxConfig:
    """Sandbox fix loop (US-003): runs the generated tests in the sandbox-controller.

    ``internal_token`` is required while ``enabled`` is true; the loader checks that.
    """

    enabled: bool = True
    controller_url: str = _opt(
        "http://pdd-sandbox-controller:8080", key="controllerURL", check=_url
    )
    internal_token: str = _opt("", key="internalToken")
    max_tries: int = _opt(5, key="maxTries", check=_positive)
    poll_interval: timedelta = _opt(timedelta(seconds=2), key="pollInterval", check=_positive)
    run_timeout: timedelta = _opt(timedelta(minutes=15), key="runTimeout", check=_positive)


@dataclass(frozen=True)
class Settings:
    """Complete validated configuration of the agent."""

    server: ServerConfig = field(default_factory=ServerConfig)
    pdd: PddConfig = field(default_factory=PddConfig)
    git_gateway: GitGatewayConfig = field(
        default_factory=GitGatewayConfig, metadata={"key": "gitGateway"}
    )
    llm: LlmConfig = field(default_factory=LlmConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    context: ContextConfig = field(default_factory=ContextConfig)
    jobs: JobsConfig = field(default_factory=JobsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
