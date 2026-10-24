"""An ``LlmClient`` wrapper with rate limiting, retry and usage recording."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

from pdd_agent.config import LimitsConfig
from pdd_agent.domain.errors import LlmError, QuotaExhaustedError
from pdd_agent.llm.usage.limiter import RateLimiter
from pdd_agent.ports.llm import LlmClient, LlmPrompt, LlmResponse


def is_retryable(error: LlmError) -> bool:
    """Return whether a failed attempt is worth repeating (429, 5xx, network errors)."""
    if error.status is None:
        return error.code == "gemini_failed"
    return error.status == 429 or error.status >= 500


class ManagedLlmClient:
    """Sends prompts through the limiter, retrying transient failures; wraps any ``LlmClient``."""

    def __init__(
        self,
        client: LlmClient,
        limiter: RateLimiter,
        config: LimitsConfig,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter
        self._config = config
        self._sleep = sleep
        self._rng = rng or random.Random()

    async def generate(self, prompt: LlmPrompt) -> LlmResponse:
        """Generate with up to ``maxAttempts`` attempts; every attempt counts against the quota.

        Usage is recorded under ``prompt.reference``.
        """
        for attempt in range(1, self._config.max_attempts + 1):
            try:
                usage_id = await self._limiter.acquire(prompt.reference)
            except QuotaExhaustedError as exc:
                raise LlmError("quota_exhausted", str(exc)) from exc
            try:
                result = await self._client.generate(prompt)
            except LlmError as exc:
                self._limiter.complete(usage_id, exc.status, 0, 0)
                delay = self._delay(exc, attempt)
                if delay is None or attempt == self._config.max_attempts:
                    raise _final(exc, attempt) from exc
                await self._sleep(delay)
            else:
                self._limiter.complete(
                    usage_id, 200, result.usage.prompt_tokens, result.usage.output_tokens
                )
                return result
        raise AssertionError("max_attempts is validated to be positive")

    def _delay(self, error: LlmError, attempt: int) -> float | None:
        """Return the wait before the next attempt, or ``None`` when retrying is pointless."""
        if not is_retryable(error):
            return None
        if error.retry_after is not None:
            limit = self._config.max_retry_after.total_seconds()
            return error.retry_after if error.retry_after <= limit else None
        ceiling = min(
            self._config.max_delay.total_seconds(),
            self._config.base_delay.total_seconds() * 2 ** (attempt - 1),
        )
        return self._rng.uniform(0, ceiling)


def _final(error: LlmError, attempts: int) -> LlmError:
    """Describe the error that ends the retries."""
    if error.status != 429:
        return error
    return LlmError(
        "rate_limited",
        f"Gemini rate limit exceeded after {attempts} attempt(s): {error.message}",
        status=429,
        retry_after=error.retry_after,
    )
