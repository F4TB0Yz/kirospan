#!/usr/bin/env python3
"""Shim de compatibilidad del gateway de Kiro.

The initial Kiro protocol integration was based on and informed by the
MIT-licensed ``mikeyobrien/pi-provider-kiro`` project. See ``NOTICE.md``.

La implementación real vive en `src/gateway/`, organizada en capas. Este módulo
se conserva porque las suites de `legacy/` importan y parchean sus símbolos por
nombre (`patch.object(kiro_gateway, "...")`), así que los seams que se parchean
deben resolverse en tiempo de llamada y no por re-exportación directa.
"""

import asyncio
import copy
import json
import logging
import os
import re
import sqlite3
import struct
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from gateway.adapters.outbound.aws_eventstream import (  # noqa: E402
    read_aws_eventstream as _read_aws_eventstream,
)
from gateway.adapters.outbound.clock import SystemClock  # noqa: E402
from gateway.adapters.outbound.kiro_auth import DesktopTokenRefresher  # noqa: E402
from gateway.adapters.outbound.kiro_models import HttpModelCatalogSource  # noqa: E402
from gateway.adapters.outbound.sqlite_tokens import SqliteTokenRepository  # noqa: E402
from gateway.application.auth import TokenService  # noqa: E402
from gateway.application.chat import ChatService  # noqa: E402
from gateway.application.models import ModelCatalog  # noqa: E402
from gateway.config import GatewayConfig  # noqa: E402
from gateway.domain.errors import AuthError, InvalidRequest, UpstreamError  # noqa: E402
from gateway.domain.images import (  # noqa: E402
    KIRO_IMAGE_FORMATS,
    KIRO_MAX_IMAGE_BYTES,
    KIRO_MAX_IMAGES,
    extract_kiro_images as _extract_kiro_images,
    kiro_image as _kiro_image,
    validate_kiro_image_count as _validate_kiro_image_count,
)
from gateway.domain.model_id import (  # noqa: E402
    MODEL_ALIASES as _MODEL_ALIASES,
    normalize_model_id as _normalize_model_id,
    sanitize_model_id as _sanitize_model_id,
)
from gateway.domain.token import token_expires_soon as _token_expires_soon  # noqa: E402
from gateway.protocols.anthropic.continuation import (  # noqa: E402
    AUTO_CONTINUATION_PROMPT as ANTHROPIC_AUTO_CONTINUATION_PROMPT,
    MAX_AUTO_CONTINUATIONS as ANTHROPIC_MAX_AUTO_CONTINUATIONS,
    build_continuation_state as _build_anthropic_continuation_state,
    response_promises_followup as _response_promises_followup,
    state_has_tool_results as _anthropic_state_has_tool_results,
)
from gateway.protocols.anthropic.requests import (  # noqa: E402
    anthropic_content_text as _anthropic_content_text,
    convert_anthropic_request as _convert_anthropic_request,
)
from gateway.protocols.anthropic.responses import (  # noqa: E402
    anthropic_response_from_openai as _anthropic_response_from_openai,
    estimate_output_tokens,
    estimate_tokens,
)
from gateway.protocols.anthropic.sse import anthropic_sse as _anthropic_sse  # noqa: E402
from gateway.protocols.kiro.bracket_tools import (  # noqa: E402
    parse_bracket_tool_calls as _parse_bracket_tool_calls,
)
from gateway.protocols.kiro.conversation import (  # noqa: E402
    TOOL_CONTINUATION_PROMPT,
    build_conversation_state as _build_conversation_state,
)
from gateway.protocols.kiro.event_decoder import (  # noqa: E402
    parse_kiro_event as _parse_kiro_event,
)
from gateway.protocols.kiro.tools import (  # noqa: E402
    add_placeholder_tools as _add_placeholder_tools,
    convert_tools_to_kiro as _convert_tools_to_kiro,
)
from gateway.protocols.openai.models import (  # noqa: E402
    FALLBACK_MODELS,
    to_openai_model_list as _to_openai_model_list,
)
from gateway.protocols.openai.non_stream_presenter import openai_non_stream  # noqa: E402
from gateway.protocols.openai.sse_presenter import openai_stream  # noqa: E402
from gateway.protocols.anthropic.sse_presenter import (  # noqa: E402
    anthropic_stream_presenter,
)


def to_openai_model_list(kiro_models: list[dict]) -> list[dict]:
    """Conserva la firma histórica: el timestamp se resuelve en el shim."""
    return _to_openai_model_list(kiro_models, int(time.time()))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("kirospan")

# ── Config ──────────────────────────────────────────────────────────────────
# Instancia única leída del entorno. Los módulos nuevos la reciben inyectada;
# aquí se exponen además los nombres planos que usaba el monolito.
CONFIG = GatewayConfig.from_env(os.environ)

REGION = CONFIG.region
MANAGEMENT_URL = CONFIG.management_url
RUNTIME_URL = CONFIG.runtime_url
HOST = CONFIG.host
PORT = CONFIG.port
GATEWAY_KEY = CONFIG.gateway_key
RUNTIME_TIMEOUT = CONFIG.runtime_timeout

_DB_PATHS = list(CONFIG.db_paths)

# ── AWS EventStream parser ──────────────────────────────────────────────────

async def read_aws_eventstream(response: httpx.Response) -> AsyncGenerator[dict, None]:
    """Seam parcheado por las suites de legacy/; delega en el adaptador."""
    async for event in _read_aws_eventstream(response):
        yield event


# ── Auth y catálogo: seams parcheados por legacy/ ───────────────────────────
# Estas funciones conservan la firma histórica del monolito y delegan en los
# servicios de `gateway.application`. Se definen aquí, y no se reexportan,
# porque las suites de legacy/ las sustituyen con patch.object sobre este
# módulo: los colaboradores se resuelven vía globals() en cada llamada.


def _find_db() -> Optional[Path]:
    return _token_repository().location_path()


def get_kiro_token() -> Optional[dict]:
    return _token_repository().load_sync()


def _save_kiro_token(token: dict) -> None:
    global _CACHED_PROFILE_ARN
    if _CACHED_PROFILE_ARN and not token.get("profile_arn"):
        token["profile_arn"] = _CACHED_PROFILE_ARN
    _token_repository().save_sync(token)


class _ShimTokenRepository:
    """Enruta el repositorio por los seams del shim, para que el patch aplique."""

    def __init__(self, config) -> None:
        self._config = config

    async def load(self) -> Optional[dict]:
        return globals()["get_kiro_token"]()

    async def save(self, token: dict) -> None:
        globals()["_save_kiro_token"](token)

    def location(self) -> Optional[str]:
        path = globals()["_find_db"]()
        return str(path) if path else None


async def _refresh_kiro_token(client: httpx.AsyncClient, token: dict) -> dict:
    """Renueva el token y lo persiste a través del seam `_save_kiro_token`."""
    try:
        refreshed = await DesktopTokenRefresher(client, CONFIG).refresh(token)
    except AuthError as exc:
        log.error(f"Kiro token refresh failed: {exc}")
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except Exception as exc:
        log.error(f"Kiro token refresh failed: {exc}")
        raise HTTPException(
            status_code=401, detail=f"Kiro token refresh failed: {exc}"
        ) from exc

    globals()["_save_kiro_token"](refreshed)
    log.info("Kiro access token refreshed and persisted")
    return refreshed


class _ShimTokenRefresher:
    """Delega en el seam `_refresh_kiro_token`, que ya persiste el token."""

    def __init__(self, client) -> None:
        self._client = client

    async def refresh(self, token: dict) -> dict:
        return await globals()["_refresh_kiro_token"](self._client, token)


_CACHED_PROFILE_ARN: str = ""


async def _ensure_token(
    client: httpx.AsyncClient,
    *,
    force_refresh: bool = False,
    rejected_access_token: Optional[str] = None,
) -> dict:
    """Entrega un token vigente. El servicio vive por cliente httpx.

    Preserva el profile_arn en memoria: si otro proceso (Kiro Desktop)
    sobreescribe el token en SQLite sin incluir profile_arn, se re-inyecta
    el último conocido.
    """
    global _CACHED_PROFILE_ARN
    service = _token_service(client)
    try:
        token = await service.ensure(
            force_refresh=force_refresh, rejected_access_token=rejected_access_token
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    # Preservar profile_arn ante race conditions con Kiro Desktop.
    token_arn = token.get("profile_arn") or token.get("profileArn") or ""
    if token_arn:
        _CACHED_PROFILE_ARN = token_arn
    elif _CACHED_PROFILE_ARN:
        token["profile_arn"] = _CACHED_PROFILE_ARN

    return token


async def fetch_models(client: httpx.AsyncClient, token: dict) -> list[dict]:
    """Devuelve los modelos disponibles y propaga el perfil al token."""
    global _CACHED_PROFILE_ARN
    catalog = await _model_catalog(client).fetch(token)
    if catalog.profile_arn:
        # El monolito comunicaba el perfil mutando el token; se conserva porque
        # los handlers lo leen de ahí.
        token["profile_arn"] = catalog.profile_arn
        _CACHED_PROFILE_ARN = catalog.profile_arn
    elif _CACHED_PROFILE_ARN:
        token["profile_arn"] = _CACHED_PROFILE_ARN
    return catalog.models

# ── FastAPI App ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(f"KiroSpan v3 — {MANAGEMENT_URL}")
    log.info(f"Runtime: {RUNTIME_URL}")
    log.info("Tool calling support enabled (based on pi-provider-kiro)")
    try:
        yield
    finally:
        client = _CLIENT_HOLDER.pop("client", None)
        if client is not None:
            await client.aclose()
        _SERVICES.clear()

# El shim delega la app real en el composition root; conserva los seams
# (`get_client`, `_services_for`, `_token_service`, `_model_catalog`,
# `_ShimRuntimeClient`) porque los tests legacy los usan directamente, sin
# pasar por HTTP.
from gateway.composition import create_app as _create_app  # noqa: E402

app = _create_app(CONFIG)

# El cliente httpx y los servicios ya no son variables globales sueltas: viven
# en contenedores para que los seams parcheables (_ensure_token, _stream_runtime,
# etc.) sigan funcionando cuando los tests legacy los llaman directamente, sin
# pasar por la app HTTP real de arriba.
_CLIENT_HOLDER: dict[str, httpx.AsyncClient] = {}
_SERVICES: dict[int, dict] = {}


def get_client() -> httpx.AsyncClient:
    client = _CLIENT_HOLDER.get("client")
    if client is None:
        client = httpx.AsyncClient(
            timeout=httpx.Timeout(CONFIG.runtime_timeout, connect=CONFIG.connect_timeout)
        )
        _CLIENT_HOLDER["client"] = client
    return client


def _services_for(client) -> dict:
    """Servicios asociados a un cliente httpx, creados una sola vez.

    Se indexan por identidad del cliente porque los tests legacy pasan dobles
    distintos por caso y cada uno debe tener su propio lock y su propia caché.
    """
    services = _SERVICES.get(id(client))
    if services is None:
        services = {
            "token": TokenService(
                _ShimTokenRepository(CONFIG),
                _ShimTokenRefresher(client),
                CONFIG.token_skew_seconds,
            ),
            "catalog": ModelCatalog(
                HttpModelCatalogSource(client, CONFIG),
                SystemClock(),
                CONFIG.model_cache_ttl,
            ),
        }
        _SERVICES[id(client)] = services
    return services


def _token_service(client) -> TokenService:
    return _services_for(client)["token"]


def _model_catalog(client) -> ModelCatalog:
    return _services_for(client)["catalog"]


def _token_repository() -> SqliteTokenRepository:
    return SqliteTokenRepository(CONFIG.db_paths)


class _ShimRuntimeClient:
    """Adapta el cliente httpx a `KiroRuntimeClient` vía los seams del shim.

    Delega en `read_aws_eventstream` y `_ensure_token` a través de `globals()`
    para que `patch.object(kiro_gateway, ...)` siga surtiendo efecto: es la
    misma técnica que usan `_ShimTokenRepository` y `_ShimTokenRefresher`.
    """

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def stream(self, *, access_token: str, state: dict, _auth_retry: bool = False):
        async with self._client.stream(
            "POST",
            RUNTIME_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
                "Accept": "application/vnd.amazon.eventstream",
                "x-amzn-codewhisperer-optout": "true",
                "x-amzn-kiro-agent-mode": "vibe",
            },
            json=state,
        ) as response:
            if response.status_code == 200:
                async for event in globals()["read_aws_eventstream"](response):
                    yield event
                return

            body = await response.aread()
            if response.status_code == 403 and not _auth_retry:
                refreshed = await globals()["_ensure_token"](
                    self._client, force_refresh=True, rejected_access_token=access_token
                )
                refreshed_arn = refreshed.get("profile_arn") or refreshed.get("profileArn")
                if refreshed_arn:
                    state["profileArn"] = refreshed_arn
                async for event in self.stream(
                    access_token=refreshed.get("access_token", ""),
                    state=state,
                    _auth_retry=True,
                ):
                    yield event
                return

            detail = body[:500].decode(errors="replace")
            log.error(f"Kiro runtime HTTP {response.status_code}: {detail}")
            raise UpstreamError(
                f"Kiro runtime error {response.status_code}: {detail}",
                status_code=response.status_code,
            )


class ChatRequest(BaseModel):
    model: str
    messages: list[dict]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[dict | str] = None


class AnthropicRequest(BaseModel):
    model: str
    messages: list[dict]
    system: Optional[str | list[dict]] = None
    stream: bool = False
    max_tokens: int = 4096
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[dict | str] = None


async def _anthropic_stream_runtime(client, access_token, state, model_id):
    """Seam parcheado por legacy/; delega en el presenter Anthropic.

    Llama a `_stream_runtime` por el nombre del módulo (vía `globals()`), no
    directamente: los tests legacy parchean `kiro_gateway._stream_runtime` y
    esperan que esta función pase por ese símbolo, con `state` como tercer
    argumento posicional.
    """
    async def stream_runtime(client_, access_token_, state_, model_id_):
        async for line in globals()["_stream_runtime"](client_, access_token_, state_, model_id_):
            yield line

    async for line in anthropic_stream_presenter(
        stream_runtime, client, access_token, state, model_id
    ):
        yield line



async def _non_stream_runtime(client, access_token, state, model_id, _auth_retry=False):
    """Seam parcheado por legacy/; delega en ChatService + el agregador OpenAI."""
    try:
        runtime_client = _ShimRuntimeClient(client)
        chat_service = ChatService(runtime_client)
        events = chat_service.run(access_token=access_token, state=state)
        return await openai_non_stream(events, model_id)
    except UpstreamError as exc:
        status = exc.status_code or 502
        raise HTTPException(status, str(exc)) from exc
    except httpx.TimeoutException:
        raise HTTPException(504, "Kiro runtime timeout")
    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Kiro runtime error: {e}")
        raise HTTPException(502, f"Kiro runtime error: {e}")



async def _stream_runtime(client, access_token, state, model_id, _auth_retry=False):
    """Seam parcheado por legacy/; delega en ChatService + el presenter OpenAI."""
    runtime_client = _ShimRuntimeClient(client)
    chat_service = ChatService(runtime_client)
    try:
        events = chat_service.run(access_token=access_token, state=state)
        async for line in openai_stream(events, model_id):
            yield line
    except Exception as exc:
        log.error(f"Stream error: {exc}")
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        error_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model_id,
            "choices": [{
                "index": 0,
                "delta": {"content": f"Gateway stream error: {exc}"},
                "finish_reason": "stop",
            }],
        }
        yield f"data: {json.dumps(error_chunk)}\n\n"
        yield "data: [DONE]\n\n"



# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    token = get_kiro_token()
    if token:
        log.info(f"✓ Kiro token found (expires: {token.get('expires_at', 'unknown')})")
    else:
        log.warning("✗ No Kiro token found. Run 'kiro-cli auth login' first.")

    uvicorn.run("kiro_gateway:app", host=HOST, port=PORT, reload=False)
