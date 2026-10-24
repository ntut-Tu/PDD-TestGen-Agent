"""Application factory: wires configuration, services and routes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pdd_agent.api.auth import JwtVerifier
from pdd_agent.api.dependencies import Container
from pdd_agent.api.errors import register_error_handlers
from pdd_agent.api.routes import router
from pdd_agent.config import Settings
from pdd_agent.service.container import open_services


def create_app(settings: Settings) -> FastAPI:
    """Build the FastAPI application for ``settings``."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_services(settings) as services:
            app.state.container = Container(JwtVerifier(settings.pdd.jwt_secret), services)
            yield

    app = FastAPI(title="PDD test-agent", lifespan=lifespan)
    app.include_router(router)
    register_error_handlers(app)
    return app
