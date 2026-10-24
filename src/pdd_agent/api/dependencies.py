"""FastAPI dependencies: the application container and the authenticated caller."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from pdd_agent.api.auth import JwtVerifier, bearer_token
from pdd_agent.domain.jobs import Principal
from pdd_agent.service.container import Services


@dataclass(frozen=True)
class Container:
    """Long-lived collaborators the routes depend on."""

    verifier: JwtVerifier
    services: Services


def container(request: Request) -> Container:
    """Dependency returning the application container."""
    return request.app.state.container


def current_principal(
    request: Request, deps: Annotated[Container, Depends(container)]
) -> Principal:
    """Dependency authenticating the caller from the bearer token."""
    return deps.verifier.verify(bearer_token(request.headers.get("Authorization")))


Deps = Annotated[Container, Depends(container)]
Caller = Annotated[Principal, Depends(current_principal)]
