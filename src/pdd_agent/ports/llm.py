"""Port of a language model provider."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LlmPrompt:
    """System instruction and user content of one generation request.

    ``response_schema`` asks the provider for JSON of that shape. ``reference`` is recorded
    with the usage of the request (for example the id of the job it belongs to).
    """

    system: str
    user: str
    response_schema: dict[str, Any] | None = None
    reference: str = ""


@dataclass(frozen=True)
class LlmUsage:
    """Tokens consumed by one request."""

    prompt_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LlmResponse:
    """Text answer of the model with its usage."""

    text: str
    usage: LlmUsage
    model: str


class LlmClient(Protocol):
    """A client that runs one generation request against a provider."""

    async def generate(self, prompt: LlmPrompt) -> LlmResponse:
        """Return the answer; raises ``LlmError`` on any failure."""
        ...
