"""Composition root: ensambla la app FastAPI a partir de `GatewayConfig`.

Es el único lugar donde se decide qué implementación concreta de cada puerto
se usa. El resto del código depende de los puertos, no de httpx ni de sqlite3.
"""

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from .adapters.inbound.http.auth_middleware import build_auth_middleware
from .adapters.inbound.http.routers.anthropic import build_anthropic_router
from .adapters.inbound.http.routers.chat import build_chat_router
from .adapters.inbound.http.routers.meta import build_meta_router
from .adapters.outbound.clock import SystemClock
from .adapters.outbound.kiro_auth import DesktopTokenRefresher
from .adapters.outbound.kiro_models import HttpModelCatalogSource
from .adapters.outbound.kiro_runtime import HttpKiroRuntimeClient
from .adapters.outbound.sqlite_tokens import SqliteTokenRepository
from .application.auth import TokenService
from .application.models import ModelCatalog
from .config import GatewayConfig


def create_app(config: GatewayConfig) -> FastAPI:
    """Construye la app con sus dependencias reales.

    El cliente httpx se crea y se cierra con el ciclo de vida de la app, no
    como un singleton de módulo.
    """
    clock = SystemClock()
    token_repository = SqliteTokenRepository(config.db_paths)
    state: dict = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.runtime_timeout, connect=config.connect_timeout)
        )
        token_service = TokenService(
            token_repository,
            DesktopTokenRefresher(client, config),
            config.token_skew_seconds,
            clock,
        )
        model_catalog = ModelCatalog(
            HttpModelCatalogSource(client, config), clock, config.model_cache_ttl
        )
        state["client"] = client
        state["token_service"] = token_service
        state["model_catalog"] = model_catalog
        try:
            yield
        finally:
            await client.aclose()
            state.clear()

    app = FastAPI(title="KiroSpan", version="3.0.0", lifespan=lifespan)
    app.middleware("http")(build_auth_middleware(config.gateway_key))

    def runtime_client_factory():
        return HttpKiroRuntimeClient(state["client"], config, state["token_service"])

    app.include_router(
        build_meta_router(
            config,
            token_repository,
            _LazyTokenService(state),
            _LazyModelCatalog(state),
            clock,
        )
    )
    app.include_router(build_chat_router(_LazyTokenService(state), _LazyModelCatalog(state), runtime_client_factory))
    app.include_router(
        build_anthropic_router(_LazyTokenService(state), _LazyModelCatalog(state), runtime_client_factory)
    )
    return app


class _LazyTokenService:
    """Reenvía a `TokenService` una vez que el lifespan lo ha creado.

    Los routers se construyen antes de que arranque el lifespan, así que no
    pueden capturar la instancia real directamente.
    """

    def __init__(self, state: dict) -> None:
        self._state = state

    async def ensure(self, **kwargs):
        return await self._state["token_service"].ensure(**kwargs)


class _LazyModelCatalog:
    def __init__(self, state: dict) -> None:
        self._state = state

    async def fetch(self, token: dict):
        return await self._state["model_catalog"].fetch(token)
