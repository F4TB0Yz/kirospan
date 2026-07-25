"""Reglas de expiración del token de Kiro.

El token se guarda en la SQLite de kiro-cli con nombres de campo inconsistentes
(`expires_at` o `expiresAt`, `access_token` o `accessToken`), así que el dominio
acepta ambos. La ausencia o el formato inválido de la fecha se tratan como
expirado: es la opción segura, provoca un refresco en lugar de un 403.
"""

from datetime import datetime, timezone

DEFAULT_SKEW_SECONDS = 120


def _first_present(token: dict, *keys: str) -> str:
    for key in keys:
        value = token.get(key)
        if value:
            return str(value)
    return ""


def access_token_of(token: dict) -> str:
    return _first_present(token, "access_token", "accessToken")


def refresh_token_of(token: dict) -> str:
    return _first_present(token, "refresh_token", "refreshToken")


def profile_arn_of(token: dict) -> str:
    return _first_present(token, "profile_arn", "profileArn")


def expires_at_of(token: dict) -> str:
    return _first_present(token, "expires_at", "expiresAt")


def token_expires_soon(
    token: dict,
    skew_seconds: int = DEFAULT_SKEW_SECONDS,
    now: float | None = None,
) -> bool:
    """Indica si el token caduca dentro del margen de seguridad."""
    expires_at = expires_at_of(token)
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    reference = datetime.now(timezone.utc).timestamp() if now is None else now
    return expiry.timestamp() <= reference + skew_seconds
