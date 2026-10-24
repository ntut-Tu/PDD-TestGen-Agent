"""Client of the Gemini ``generateContent`` REST API; implements ``LlmClient``."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from pdd_agent.config import LlmConfig
from pdd_agent.domain.errors import LlmError
from pdd_agent.ports.llm import LlmPrompt, LlmResponse, LlmUsage

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com"


class GeminiClient:
    """Sends one prompt to Gemini and returns the text of its first candidate."""

    def __init__(self, client: httpx.AsyncClient, config: LlmConfig) -> None:
        self._client = client
        self._model = config.model
        base_url = config.base_url or DEFAULT_BASE_URL
        self._url = f"{base_url.rstrip('/')}/v1beta/models/{config.model}:generateContent"
        self._headers = {"x-goog-api-key": config.api_key}
        self._timeout = config.request_timeout.total_seconds()

    async def generate(self, prompt: LlmPrompt) -> LlmResponse:
        """Run one generation request; raises ``LlmError`` on any failure."""
        body = {
            "systemInstruction": {"parts": [{"text": prompt.system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt.user}]}],
        }
        if prompt.response_schema is not None:
            body["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseSchema": prompt.response_schema,
            }
        try:
            response = await self._client.post(
                self._url, json=body, headers=self._headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            raise LlmError("gemini_failed", f"Gemini request failed: {exc!r}") from exc
        if response.status_code == 404:
            raise LlmError(
                "model_unavailable",
                f"Gemini model {self._model!r} is not available for this API key; "
                f"change llm.model in the test-agent values (testAgent.llm.model): "
                f"{_http_message(response)}",
                status=404,
            )
        if response.status_code != 200:
            raise LlmError(
                "gemini_failed",
                _http_message(response),
                status=response.status_code,
                retry_after=_retry_after(response),
            )
        return self._parse(response)

    def _parse(self, response: httpx.Response) -> LlmResponse:
        try:
            payload = response.json()
            usage = _usage(payload)
            candidate = payload["candidates"][0]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            reason = _block_reason(response)
            raise LlmError("invalid_response", reason or "Gemini returned no candidate") from exc
        if candidate.get("finishReason") == "MAX_TOKENS":
            raise LlmError("output_truncated", "Gemini stopped before finishing the answer")
        return LlmResponse(text=_text(candidate), usage=usage, model=self._model)


def _usage(payload: dict[str, Any]) -> LlmUsage:
    meta = payload.get("usageMetadata") or {}
    return LlmUsage(
        prompt_tokens=int(meta.get("promptTokenCount", 0)),
        output_tokens=int(meta.get("candidatesTokenCount", 0)),
    )


def _text(candidate: dict[str, Any]) -> str:
    try:
        return "".join(p.get("text", "") for p in candidate["content"]["parts"])
    except (KeyError, TypeError) as exc:
        raise LlmError("invalid_response", "Gemini answer is not the expected JSON") from exc


def _block_reason(response: httpx.Response) -> str | None:
    try:
        reason = response.json().get("promptFeedback", {}).get("blockReason")
    except (ValueError, AttributeError):
        return None
    return f"Gemini blocked the prompt: {reason}" if reason else None


def _retry_after(response: httpx.Response) -> float | None:
    """Parse ``Retry-After`` given as delay seconds or as an HTTP date."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    if value.strip().isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max((moment - datetime.now(UTC)).total_seconds(), 0.0)


def _http_message(response: httpx.Response) -> str:
    try:
        detail = response.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        detail = response.text[:200]
    return f"Gemini answered {response.status_code}: {detail}"
