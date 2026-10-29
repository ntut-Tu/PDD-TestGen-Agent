"""Domain errors. They carry a machine readable code and a message, never an HTTP status."""

from __future__ import annotations

from datetime import UTC, datetime

from pdd_agent.domain.timeutil import to_iso

UNSUPPORTED_MESSAGE = (
    "Only Java repositories built with Maven or Gradle are supported "
    "(no pom.xml, build.gradle or build.gradle.kts at the repository root)."
)


class DomainError(Exception):
    """Base of every error that is reported to users with a ``code`` and a ``message``."""

    default_code = "internal"

    def __init__(self, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or self.default_code


class UnauthorizedError(DomainError):
    """The caller could not be authenticated."""

    default_code = "unauthorized"


class ForbiddenError(DomainError):
    """The caller may not access the resource."""

    default_code = "forbidden"


class NotFoundError(DomainError):
    """The requested resource does not exist."""

    default_code = "not_found"


class UpstreamError(DomainError):
    """A service the agent depends on failed."""

    default_code = "upstream_failed"


class InvalidRequestError(DomainError):
    """The request is well formed but cannot be served as asked."""


class SelectionError(InvalidRequestError):
    """A selected path is invalid or not part of the repository."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message, code)


class UnsupportedLanguageError(InvalidRequestError):
    """The repository is not a Java Maven or Gradle project."""

    default_code = "unsupported_language"

    def __init__(self) -> None:
        super().__init__(UNSUPPORTED_MESSAGE)


class PullChangedError(DomainError):
    """The pull request head moved after the job was created."""

    default_code = "pull_changed"

    def __init__(self) -> None:
        super().__init__("the pull request head changed, generate again")


class SourceError(DomainError):
    """The source repository could not serve a request."""

    default_code = "gateway_failed"

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LlmError(DomainError):
    """A language model request failed or returned an unusable response."""

    def __init__(
        self,
        code: str,
        message: str,
        status: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, code)
        self.status = status
        self.retry_after = retry_after


class QuotaExhaustedError(DomainError):
    """The daily language model request allowance is used up."""

    default_code = "quota_exhausted"

    def __init__(self, reset_at: float, now: float) -> None:
        self.reset_at = reset_at
        self.retry_after = max(int(reset_at - now) + 1, 1)
        reset = to_iso(datetime.fromtimestamp(reset_at, UTC))
        super().__init__(f"daily Gemini quota is used up, it resets at {reset}")


class SandboxError(DomainError):
    """The sandbox-controller could not serve a request.

    ``code`` is a sandbox reason: ``sandbox_unavailable`` when the controller cannot be
    reached or reports a failing service, ``internal`` for anything unexpected.
    """

    def __init__(self, message: str, code: str = "internal") -> None:
        super().__init__(message, code)
