"""Registry of the supported language model providers."""

from __future__ import annotations

from collections.abc import Callable

import httpx

from pdd_agent.config import LlmConfig
from pdd_agent.llm.google_genai import GeminiClient
from pdd_agent.ports.llm import LlmClient

# Maps ``llm.provider`` to the factory of its client. Add new providers here and to
# ``pdd_agent.config.LLM_PROVIDERS``.
PROVIDERS: dict[str, Callable[[httpx.AsyncClient, LlmConfig], LlmClient]] = {
    "google_genai": GeminiClient,
}


def create_client(http: httpx.AsyncClient, config: LlmConfig) -> LlmClient:
    """Build the client of ``config.provider``."""
    return PROVIDERS[config.provider](http, config)
