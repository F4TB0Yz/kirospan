"""Obtención y renovación del token de Kiro.

Sustituye al lock a nivel de módulo del monolito: el lock se crea con el
servicio, ya dentro del event loop, y no en tiempo de importación.

El doble chequeo tras adquirir el lock evita renovaciones en cascada cuando
varias peticiones reciben un 403 a la vez: la primera renueva y las demás
encuentran en la base de datos un token distinto del rechazado y lo reutilizan.
"""

import asyncio
import logging

from ..domain.errors import AuthError
from ..domain.token import access_token_of, token_expires_soon

log = logging.getLogger("kirospan")

NO_TOKEN_MESSAGE = "No Kiro token found. Run 'kiro-cli auth login'."


class TokenService:
    """Caso de uso: entregar un access token válido."""

    def __init__(self, repository, refresher, skew_seconds: int, clock=None) -> None:
        self._repository = repository
        self._refresher = refresher
        self._skew_seconds = skew_seconds
        self._clock = clock
        self._lock = asyncio.Lock()

    def _expires_soon(self, token: dict) -> bool:
        now = self._clock.now() if self._clock else None
        return token_expires_soon(token, self._skew_seconds, now)

    async def _load(self) -> dict:
        token = await self._repository.load()
        if not token:
            raise AuthError(NO_TOKEN_MESSAGE)
        return token

    async def ensure(
        self,
        *,
        force_refresh: bool = False,
        rejected_access_token: str | None = None,
    ) -> dict:
        """Devuelve un token vigente, renovándolo si hace falta."""
        token = await self._load()
        if not force_refresh and not self._expires_soon(token):
            return token

        async with self._lock:
            latest = await self._load()

            # Otra petición ya renovó mientras se esperaba el lock.
            latest_access = access_token_of(latest)
            if (
                rejected_access_token
                and latest_access
                and latest_access != rejected_access_token
                and not self._expires_soon(latest)
            ):
                return latest

            if not force_refresh and not self._expires_soon(latest):
                return latest

            refreshed = await self._refresher.refresh(latest)
            await self._repository.save(refreshed)
            log.info("Kiro access token refreshed and persisted")
            return refreshed
