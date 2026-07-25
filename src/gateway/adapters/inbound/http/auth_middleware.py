"""Middleware de autenticación por API key.

La comparación usa `secrets.compare_digest` para no filtrar por temporización
cuánto de la clave enviada coincide con la real.
"""

import secrets

from fastapi.responses import JSONResponse

UNAUTHENTICATED_PATHS = {"/health"}

INVALID_KEY_RESPONSE = {
    "type": "error",
    "error": {"type": "authentication_error", "message": "Invalid API key"},
}


def _matches(candidate: str, expected: str) -> bool:
    return bool(candidate) and secrets.compare_digest(candidate, expected)


def build_auth_middleware(gateway_key: str):
    """Construye el middleware de auth para una clave concreta."""

    async def check_auth(request, call_next):
        if request.url.path in UNAUTHENTICATED_PATHS:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        bearer_token = auth.removeprefix("Bearer ") if auth.startswith("Bearer ") else ""
        api_key = request.headers.get("x-api-key", "")

        if _matches(bearer_token, gateway_key) or _matches(api_key, gateway_key):
            return await call_next(request)

        return JSONResponse(status_code=401, content=INVALID_KEY_RESPONSE)

    return check_auth
