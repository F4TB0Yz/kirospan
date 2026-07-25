"""Cliente HTTP del runtime de Kiro.

Concentra en un solo sitio los headers de la petición y el reintento tras un
403, que en el monolito estaban duplicados entre la ruta de streaming y la de
respuesta única, con el riesgo de divergir.
"""

import logging
from typing import AsyncIterator

import httpx

from ...domain.errors import UpstreamError
from ...domain.token import access_token_of, profile_arn_of
from .aws_eventstream import read_aws_eventstream

ERROR_DETAIL_LIMIT = 500

RUNTIME_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/vnd.amazon.eventstream",
    "x-amzn-codewhisperer-optout": "true",
    "x-amzn-kiro-agent-mode": "vibe",
}

log = logging.getLogger("kirospan")


class HttpKiroRuntimeClient:
    """Implementa `KiroRuntimeClient` sobre httpx."""

    def __init__(self, client: httpx.AsyncClient, config, token_service=None) -> None:
        self._client = client
        self._config = config
        self._token_service = token_service

    def _headers(self, access_token: str) -> dict:
        return {"Authorization": f"Bearer {access_token}", **RUNTIME_HEADERS}

    async def stream(
        self,
        *,
        access_token: str,
        state: dict,
        _auth_retry: bool = False,
    ) -> AsyncIterator[dict]:
        """Emite los payloads del EventStream, renovando el token ante un 403."""
        try:
            async with self._client.stream(
                "POST",
                self._config.runtime_url,
                headers=self._headers(access_token),
                json=state,
            ) as response:
                if response.status_code == 200:
                    async for event in read_aws_eventstream(response):
                        yield event
                    return

                body = await response.aread()
                if response.status_code == 403 and not _auth_retry and self._token_service:
                    async for event in self._retry_with_fresh_token(access_token, state):
                        yield event
                    return

                detail = body[:ERROR_DETAIL_LIMIT].decode(errors="replace")
                log.error(f"Kiro runtime HTTP {response.status_code}: {detail}")
                raise UpstreamError(
                    f"Kiro runtime error {response.status_code}: {detail}",
                    status_code=response.status_code,
                )
        except httpx.TimeoutException as exc:
            raise UpstreamError("Kiro runtime timeout", status_code=504) from exc

    async def _retry_with_fresh_token(
        self, rejected_access_token: str, state: dict
    ) -> AsyncIterator[dict]:
        refreshed = await self._token_service.ensure(
            force_refresh=True, rejected_access_token=rejected_access_token
        )
        profile_arn = profile_arn_of(refreshed)
        if profile_arn:
            state["profileArn"] = profile_arn
        async for event in self.stream(
            access_token=access_token_of(refreshed), state=state, _auth_retry=True
        ):
            yield event
