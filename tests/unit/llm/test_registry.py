import httpx

from pdd_agent.config import LLM_PROVIDERS, LlmConfig
from pdd_agent.llm.google_genai import GeminiClient
from pdd_agent.llm.registry import PROVIDERS, create_client


def test_registry_matches_the_configurable_providers():
    assert tuple(PROVIDERS) == LLM_PROVIDERS


async def test_google_genai_creates_the_gemini_client():
    async with httpx.AsyncClient() as http:
        assert isinstance(create_client(http, LlmConfig(api_key="k")), GeminiClient)
