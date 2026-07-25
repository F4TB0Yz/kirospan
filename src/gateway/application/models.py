"""Catálogo de modelos con caché por instancia.

Sustituye al diccionario `_model_cache` a nivel de módulo. El `profileArn` se
devuelve junto a los modelos en lugar de escribirse dentro del diccionario del
token, que era un canal lateral difícil de seguir.
"""

import logging
from dataclasses import dataclass, field

from ..domain.token import access_token_of, profile_arn_of

log = logging.getLogger("kirospan")


@dataclass(frozen=True, slots=True)
class Catalog:
    """Modelos disponibles y el perfil con el que se obtuvieron."""

    models: list[dict] = field(default_factory=list)
    profile_arn: str = ""


class ModelCatalog:
    """Caso de uso: listar los modelos disponibles, con caché temporal."""

    def __init__(self, source, clock, ttl_seconds: int) -> None:
        self._source = source
        self._clock = clock
        self._ttl_seconds = ttl_seconds
        self._catalog = Catalog()
        self._fetched_at = 0.0

    def _is_fresh(self) -> bool:
        return bool(self._catalog.models) and (
            self._clock.now() - self._fetched_at < self._ttl_seconds
        )

    async def _resolve_profile_arn(self, token: dict) -> str:
        """El perfil viene en el token; si no, se pide al plano de gestión."""
        profile_arn = profile_arn_of(token) or self._catalog.profile_arn
        if profile_arn:
            return profile_arn

        profiles = await self._source.list_profiles(access_token_of(token))
        return profiles[0].get("arn", "") if profiles else ""

    async def fetch(self, token: dict) -> Catalog:
        """Devuelve el catálogo, usando la caché mientras siga vigente."""
        profile_arn = await self._resolve_profile_arn(token)

        if self._is_fresh():
            return Catalog(self._catalog.models, profile_arn or self._catalog.profile_arn)

        if not profile_arn:
            return self._catalog

        models = await self._source.list_models(access_token_of(token), profile_arn)
        if models:
            self._catalog = Catalog(models, profile_arn)
            self._fetched_at = self._clock.now()
            return self._catalog

        return Catalog(self._catalog.models, profile_arn)
