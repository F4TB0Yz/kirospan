"""Tests unitarios del middleware de auth, aislados de FastAPI real."""

import pytest

from gateway.adapters.inbound.http.auth_middleware import build_auth_middleware


class _FakeRequest:
    def __init__(self, path: str, headers: dict) -> None:
        self.url = type("URL", (), {"path": path})()
        self.headers = headers


async def _call_next(request):
    return "ok"


class TestAuthMiddleware:
    async def test_health_bypasses_auth(self):
        check_auth = build_auth_middleware("secreta")
        response = await check_auth(_FakeRequest("/health", {}), _call_next)
        assert response == "ok"

    async def test_bearer_token_is_accepted(self):
        check_auth = build_auth_middleware("secreta")
        request = _FakeRequest("/v1/props", {"Authorization": "Bearer secreta"})
        assert await check_auth(request, _call_next) == "ok"

    async def test_x_api_key_is_accepted(self):
        check_auth = build_auth_middleware("secreta")
        request = _FakeRequest("/v1/props", {"x-api-key": "secreta"})
        assert await check_auth(request, _call_next) == "ok"

    async def test_wrong_key_is_rejected(self):
        check_auth = build_auth_middleware("secreta")
        request = _FakeRequest("/v1/props", {"x-api-key": "incorrecta"})
        response = await check_auth(request, _call_next)
        assert response.status_code == 401

    async def test_missing_headers_are_rejected(self):
        check_auth = build_auth_middleware("secreta")
        response = await check_auth(_FakeRequest("/v1/props", {}), _call_next)
        assert response.status_code == 401

    async def test_authorization_without_bearer_prefix_is_rejected(self):
        # "Authorization: secreta" sin el prefijo Bearer no debe autenticar.
        check_auth = build_auth_middleware("secreta")
        request = _FakeRequest("/v1/props", {"Authorization": "secreta"})
        response = await check_auth(request, _call_next)
        assert response.status_code == 401

    async def test_empty_gateway_key_never_matches_empty_header(self):
        # secrets.compare_digest("", "") es True; _matches lo evita exigiendo
        # que el candidato no esté vacío.
        check_auth = build_auth_middleware("")
        response = await check_auth(_FakeRequest("/v1/props", {}), _call_next)
        assert response.status_code == 401

    @pytest.mark.parametrize("prefix_length", [0, 1, 5, 20])
    async def test_comparison_uses_compare_digest_not_equality(self, prefix_length, monkeypatch):
        # No se puede medir tiempo de forma fiable en un test unitario; se
        # verifica en su lugar que la implementación llama a compare_digest
        # y no a `==`, que es la propiedad que importa.
        import gateway.adapters.inbound.http.auth_middleware as module

        calls = []
        original = module.secrets.compare_digest

        def spy(a, b):
            calls.append((a, b))
            return original(a, b)

        monkeypatch.setattr(module.secrets, "compare_digest", spy)
        check_auth = build_auth_middleware("secreta")
        candidate = "secreta"[:prefix_length] + "x" * 10
        await check_auth(
            _FakeRequest("/v1/props", {"x-api-key": candidate}), _call_next
        )
        assert calls, "se esperaba que compare_digest se invocara"
