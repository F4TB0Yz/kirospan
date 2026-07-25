"""Puertos que la capa de aplicación necesita del exterior.

Son `Protocol`, no clases base: los adaptadores no heredan de aquí, solo
cumplen la forma. Así la aplicación no depende de httpx ni de sqlite3, y los
tests pueden pasar dobles sin registrar nada.
"""

from typing import AsyncIterator, Protocol


class TokenRepository(Protocol):
    """Persistencia del token de Kiro (hoy, la SQLite de kiro-cli)."""

    async def load(self) -> dict | None:
        """Devuelve el token guardado, o `None` si no hay credenciales."""
        ...

    async def save(self, token: dict) -> None:
        """Persiste el token refrescado."""
        ...

    def location(self) -> str | None:
        """Ruta del almacén, para diagnóstico."""
        ...


class TokenRefresher(Protocol):
    """Renovación del access token contra el servicio de auth de Kiro."""

    async def refresh(self, token: dict) -> dict:
        """Devuelve un token nuevo a partir del refresh token."""
        ...


class KiroRuntimeClient(Protocol):
    """Acceso al runtime que genera las respuestas del asistente."""

    def stream(self, *, access_token: str, state: dict) -> AsyncIterator[dict]:
        """Emite los payloads del EventStream ya decodificados."""
        ...


class ModelCatalogSource(Protocol):
    """Plano de gestión de Kiro: perfiles y modelos disponibles."""

    async def list_profiles(self, access_token: str) -> list[dict]:
        ...

    async def list_models(self, access_token: str, profile_arn: str) -> list[dict]:
        ...


class Clock(Protocol):
    """Reloj inyectable, para que los TTL y la expiración sean testeables."""

    def now(self) -> float:
        ...
