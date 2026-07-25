"""Catálogo de modelos del plano de gestión de Kiro."""

import logging

import httpx

log = logging.getLogger("kirospan")


class HttpModelCatalogSource:
    """Implementa `ModelCatalogSource` sobre httpx."""

    def __init__(self, client: httpx.AsyncClient, config) -> None:
        self._client = client
        self._config = config

    async def list_profiles(self, access_token: str) -> list[dict]:
        try:
            response = await self._client.post(
                f"{self._config.management_url}/List-Available-Profiles",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
                json={},
            )
        except httpx.HTTPError as exc:
            log.warning(f"Failed to fetch profiles: {exc}")
            return []

        if response.status_code != 200:
            log.warning(f"Failed to fetch profiles: HTTP {response.status_code}")
            return []
        return response.json().get("profiles", [])

    async def list_models(self, access_token: str, profile_arn: str) -> list[dict]:
        try:
            response = await self._client.get(
                f"{self._config.management_url}/List-Available-Models",
                params={"origin": "KIRO_CLI", "profileArn": profile_arn},
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            log.warning(f"Failed to fetch models: {exc}")
            return []

        if response.status_code != 200:
            log.warning(f"Failed to fetch models: HTTP {response.status_code}")
            return []
        return response.json().get("models", [])
