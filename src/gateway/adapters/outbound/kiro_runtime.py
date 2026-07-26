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
from .aws_eventstream import EVENTSTREAM_EXCEPTION_KEY, read_kiro_frames

ERROR_DETAIL_LIMIT = 500

_TRANSIENT_EXCEPTION_TYPES = {
    "internalservererror",
    "internalserverexception",
    "serviceunavailableexception",
    "throttlingexception",
}
_TRANSIENT_MESSAGE_MARKERS = (
    "unexpected error",
    "please try again",
    "temporarily unavailable",
    "internal server error",
)

RUNTIME_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/vnd.amazon.eventstream",
    "x-amzn-codewhisperer-optout": "true",
    "x-amzn-kiro-agent-mode": "vibe",
}

log = logging.getLogger("kirospan")


def _is_retryable_stream_exception(exception_type: str, message: str) -> bool:
    normalized_type = exception_type.replace("_", "").replace("-", "").lower()
    normalized_message = message.lower()
    return normalized_type in _TRANSIENT_EXCEPTION_TYPES or (
        normalized_type in {"error", "serviceerror", "unknown"}
        and any(marker in normalized_message for marker in _TRANSIENT_MESSAGE_MARKERS)
    )


def _request_id_of(response) -> str | None:
    headers = getattr(response, "headers", {})
    return (
        headers.get("x-amzn-requestid")
        or headers.get("x-amzn-request-id")
        or headers.get("x-amz-request-id")
    )


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
                    async for frame in read_kiro_frames(response):
                        event = frame.payload
                        exception = event.get(EVENTSTREAM_EXCEPTION_KEY)
                        if exception:
                            exception_type = str(exception.get("exception_type") or "unknown")
                            message = str(
                                exception.get("message") or "Unknown Kiro stream error"
                            )
                            request_id = _request_id_of(response)
                            retryable = _is_retryable_stream_exception(
                                exception_type, message
                            )
                            log.error(
                                "Kiro EventStream exception request_id=%s "
                                "exception_type=%s retryable=%s",
                                request_id or "unknown",
                                exception_type,
                                retryable,
                            )
                            raise UpstreamError(
                                f"Kiro stream error: {message}",
                                status_code=502,
                                retryable=retryable,
                                request_id=request_id,
                            )
                        if frame.event_type == "reasoningContentEvent" and (
                            "reasoningContent" not in event
                        ):
                            event = {"reasoningContent": event}
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
