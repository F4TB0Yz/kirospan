"""Renovación del token contra el servicio de auth de Kiro Desktop."""

import logging
from datetime import datetime, timedelta, timezone

from ...domain.errors import AuthError
from ...domain.token import refresh_token_of

DEFAULT_EXPIRES_IN_SECONDS = 3600
USER_AGENT = "kiro-gateway"

log = logging.getLogger("kiro-gateway")


def _expiry_iso(expires_in: int) -> str:
    expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    return expiry.isoformat().replace("+00:00", "Z")


class DesktopTokenRefresher:
    """Implementa `TokenRefresher` contra `prod.<region>.auth.desktop.kiro.dev`."""

    def __init__(self, client, config) -> None:
        self._client = client
        self._config = config

    async def refresh(self, token: dict) -> dict:
        refresh_token = refresh_token_of(token)
        if not refresh_token:
            raise AuthError("Kiro refresh token not found. Run 'kiro-cli auth login'.")

        region = token.get("region") or self._config.region
        response = await self._client.post(
            self._config.refresh_url(region),
            headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
            json={"refreshToken": refresh_token},
        )
        if response.status_code != 200:
            raise AuthError(f"desktop refresh returned HTTP {response.status_code}")

        data = response.json()
        access_token = data.get("accessToken")
        if not access_token:
            raise AuthError("desktop refresh response did not include accessToken")

        expires_in = int(data.get("expiresIn") or DEFAULT_EXPIRES_IN_SECONDS)
        refreshed = dict(token)
        refreshed["access_token"] = access_token
        refreshed["refresh_token"] = data.get("refreshToken") or refresh_token
        refreshed["expires_at"] = _expiry_iso(expires_in)
        if data.get("profileArn"):
            refreshed["profile_arn"] = data["profileArn"]
        refreshed["region"] = region
        return refreshed
