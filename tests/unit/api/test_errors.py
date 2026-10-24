import pytest

from pdd_agent.api.errors import to_api_error
from pdd_agent.domain.errors import (
    DomainError,
    ForbiddenError,
    LlmError,
    NotFoundError,
    QuotaExhaustedError,
    SelectionError,
    UnauthorizedError,
    UnsupportedLanguageError,
    UpstreamError,
)


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (UnauthorizedError("x"), 401, "unauthorized"),
        (ForbiddenError("x"), 403, "forbidden"),
        (NotFoundError("x"), 404, "not_found"),
        (SelectionError("path_not_found", "x"), 422, "path_not_found"),
        (UnsupportedLanguageError(), 422, "unsupported_language"),
        (UpstreamError("x"), 502, "upstream_failed"),
        (LlmError("gemini_failed", "x"), 500, "gemini_failed"),
        (DomainError("x"), 500, "internal"),
    ],
)
def test_domain_errors_map_to_http_status(error, status, code):
    mapped = to_api_error(error)
    assert (mapped.status, mapped.code, mapped.message) == (status, code, error.message)


def test_quota_exhaustion_maps_to_429_with_retry_after():
    mapped = to_api_error(QuotaExhaustedError(reset_at=1000.0, now=400.0))
    assert mapped.status == 429 and mapped.code == "quota_exhausted"
    assert mapped.headers == {"Retry-After": "601"}
