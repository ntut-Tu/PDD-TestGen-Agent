import httpx
import pytest

from pdd_agent.config import LlmConfig
from pdd_agent.domain.errors import LlmError
from pdd_agent.llm.google_genai import GeminiClient
from pdd_agent.ports.llm import LlmPrompt
from tests.conftest import GEMINI, gemini_answer

SCHEMA = {"type": "OBJECT"}


async def generate(upstreams, response=None, prompt=None, **kw):
    if response is not None:
        upstreams.gemini_response = response
    config = LlmConfig(api_key="key", base_url=GEMINI + "/", **kw)
    async with httpx.AsyncClient() as http:
        return await GeminiClient(http, config).generate(prompt or LlmPrompt("sys", "user", SCHEMA))


async def test_request_shape_and_parsing(upstreams):
    answer = gemini_answer([{"path": "a", "content": "b"}], prompt=11, output=22)
    result = await generate(upstreams, httpx.Response(200, json=answer))
    assert result.text == '{"files": [{"path": "a", "content": "b"}]}'
    assert (result.usage.prompt_tokens, result.usage.output_tokens) == (11, 22)
    assert result.model == "gemini-3.8-flash"
    request = upstreams.gemini.calls.last.request
    assert request.headers["x-goog-api-key"] == "key"
    body = request.read().decode()
    assert '"responseMimeType":"application/json"' in body.replace(" ", "")
    assert '"systemInstruction"' in body and "responseSchema" in body


async def test_free_text_prompt_sends_no_response_schema(upstreams):
    await generate(upstreams, prompt=LlmPrompt("sys", "user"))
    body = upstreams.gemini.calls.last.request.read().decode()
    assert "generationConfig" not in body


@pytest.mark.parametrize(
    ("response", "code"),
    [
        (httpx.Response(500, json={"error": {"message": "boom"}}), "gemini_failed"),
        (httpx.Response(400, text="plain"), "gemini_failed"),
        (httpx.Response(200, text="not json"), "invalid_response"),
        (httpx.Response(200, json={"candidates": []}), "invalid_response"),
        (
            httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
            "invalid_response",
        ),
        (
            httpx.Response(
                200,
                json={
                    "candidates": [
                        {"content": {"parts": [{"text": "{}"}]}, "finishReason": "MAX_TOKENS"}
                    ]
                },
            ),
            "output_truncated",
        ),
        (
            httpx.Response(200, json={"candidates": [{"finishReason": "SAFETY"}]}),
            "invalid_response",
        ),
    ],
)
async def test_failures(upstreams, response, code):
    with pytest.raises(LlmError) as err:
        await generate(upstreams, response)
    assert err.value.code == code


async def test_unknown_model_is_reported_as_model_unavailable(upstreams):
    gone = httpx.Response(404, json={"error": {"message": "model is no longer available"}})
    with pytest.raises(LlmError) as err:
        await generate(upstreams, gone)
    assert err.value.code == "model_unavailable" and err.value.status == 404
    assert "'gemini-3.8-flash'" in err.value.message
    assert "testAgent.llm.model" in err.value.message
    assert "no longer available" in err.value.message


async def test_http_status_is_kept_and_blocked_reason_reported(upstreams):
    with pytest.raises(LlmError) as err:
        await generate(upstreams, httpx.Response(503, text="x"))
    assert err.value.status == 503
    with pytest.raises(LlmError, match="blocked the prompt: SAFETY"):
        await generate(
            upstreams, httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
        )


async def test_transport_error(upstreams):
    upstreams.gemini.side_effect = httpx.ConnectTimeout("slow")
    with pytest.raises(LlmError) as err:
        await generate(upstreams)
    assert err.value.code == "gemini_failed" and err.value.status is None
