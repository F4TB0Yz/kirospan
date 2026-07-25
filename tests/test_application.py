"""Tests de los casos de uso de `gateway.application`.

Sustituyen la verificación del estado global del monolito (lock a nivel de
módulo, `_model_cache` como diccionario suelto): aquí cada test crea su propia
instancia, así que no hay fuga de estado entre tests.
"""

import pytest

from gateway.application.auth import TokenService
from gateway.application.models import ModelCatalog
from gateway.domain.errors import AuthError


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def now(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _FakeRepository:
    def __init__(self, token: dict | None) -> None:
        self.token = token
        self.saved = []

    async def load(self) -> dict | None:
        return self.token

    async def save(self, token: dict) -> None:
        self.saved.append(token)
        self.token = token

    def location(self):
        return None


class _FakeRefresher:
    def __init__(self, refreshed: dict) -> None:
        self.refreshed = refreshed
        self.calls = 0

    async def refresh(self, token: dict) -> dict:
        self.calls += 1
        return self.refreshed


class _FailingRefresher:
    async def refresh(self, token: dict) -> dict:
        raise AuthError("no se pudo renovar")


VALID_TOKEN = {"access_token": "vigente", "expires_at": "2099-01-01T00:00:00Z"}
EXPIRED_TOKEN = {"access_token": "caducado", "expires_at": "2000-01-01T00:00:00Z"}


class TestTokenService:
    async def test_valid_token_is_returned_without_refreshing(self):
        refresher = _FakeRefresher({"access_token": "no-deberia-usarse"})
        service = TokenService(_FakeRepository(VALID_TOKEN), refresher, skew_seconds=120)

        token = await service.ensure()

        assert token == VALID_TOKEN
        assert refresher.calls == 0

    async def test_expired_token_is_refreshed_and_persisted(self):
        repository = _FakeRepository(EXPIRED_TOKEN)
        refresher = _FakeRefresher({"access_token": "fresco"})
        service = TokenService(repository, refresher, skew_seconds=120)

        token = await service.ensure()

        assert token == {"access_token": "fresco"}
        assert repository.saved == [{"access_token": "fresco"}]

    async def test_missing_token_raises_auth_error(self):
        service = TokenService(_FakeRepository(None), _FakeRefresher({}), skew_seconds=120)
        with pytest.raises(AuthError, match="No Kiro token found"):
            await service.ensure()

    async def test_force_refresh_ignores_a_valid_token(self):
        refresher = _FakeRefresher({"access_token": "forzado"})
        service = TokenService(_FakeRepository(VALID_TOKEN), refresher, skew_seconds=120)

        token = await service.ensure(force_refresh=True)

        assert token == {"access_token": "forzado"}
        assert refresher.calls == 1

    async def test_rejected_token_reuses_a_token_already_refreshed_by_another_call(self):
        # Otra petición ya renovó mientras se esperaba el lock: no debe volver
        # a llamar al refresher.
        repository = _FakeRepository(VALID_TOKEN)
        refresher = _FakeRefresher({"access_token": "no-deberia-usarse"})
        service = TokenService(repository, refresher, skew_seconds=120)

        token = await service.ensure(force_refresh=True, rejected_access_token="otro-distinto")

        assert token == VALID_TOKEN
        assert refresher.calls == 0

    async def test_refresh_failure_propagates(self):
        service = TokenService(_FakeRepository(EXPIRED_TOKEN), _FailingRefresher(), skew_seconds=120)
        with pytest.raises(AuthError, match="no se pudo renovar"):
            await service.ensure()

    async def test_clock_controls_expiration(self):
        clock = _FakeClock(start=1000.0)
        token = {"access_token": "a", "expires_at": "1970-01-01T00:20:00Z"}  # epoch 1200
        refresher = _FakeRefresher({"access_token": "renovado"})
        service = TokenService(_FakeRepository(token), refresher, skew_seconds=60, clock=clock)

        # 1000 + 60 < 1200: todavía vigente.
        assert (await service.ensure())["access_token"] == "a"

        clock.advance(200)  # ahora 1200: dentro del margen de 60 s, expira.
        assert (await service.ensure())["access_token"] == "renovado"

    async def test_concurrent_403_retries_only_refresh_once(self):
        # Escenario real: varias llamadas en curso reciben un 403 con el mismo
        # access_token rechazado y llaman a ensure(force_refresh=True,
        # rejected_access_token=...). Solo la primera debe refrescar; las
        # demas deben ver, tras el lock, un token ya distinto del rechazado.
        import asyncio

        repository = _FakeRepository(EXPIRED_TOKEN)
        refresher = _FakeRefresher({"access_token": "unico", "expires_at": "2099-01-01T00:00:00Z"})
        service = TokenService(repository, refresher, skew_seconds=120)

        results = await asyncio.gather(
            *(
                service.ensure(force_refresh=True, rejected_access_token="caducado")
                for _ in range(5)
            )
        )

        assert all(result["access_token"] == "unico" for result in results)
        assert refresher.calls == 1

    async def test_independent_instances_do_not_share_state(self):
        # Antes, el lock era del modulo: una instancia bloqueaba a todas.
        service_a = TokenService(_FakeRepository(EXPIRED_TOKEN), _FakeRefresher({"access_token": "a"}), 120)
        service_b = TokenService(_FakeRepository(EXPIRED_TOKEN), _FakeRefresher({"access_token": "b"}), 120)

        token_a, token_b = await service_a.ensure(), await service_b.ensure()

        assert token_a["access_token"] == "a"
        assert token_b["access_token"] == "b"


class _FakeCatalogSource:
    def __init__(self, profiles=None, models=None) -> None:
        self.profiles = profiles or []
        self.models = models or []
        self.profile_calls = 0
        self.model_calls = 0

    async def list_profiles(self, access_token: str) -> list[dict]:
        self.profile_calls += 1
        return self.profiles

    async def list_models(self, access_token: str, profile_arn: str) -> list[dict]:
        self.model_calls += 1
        return self.models


class TestModelCatalog:
    async def test_profile_arn_from_the_token_is_used_directly(self):
        source = _FakeCatalogSource(models=[{"modelId": "auto"}])
        catalog = ModelCatalog(source, _FakeClock(), ttl_seconds=300)

        result = await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})

        assert result.models == [{"modelId": "auto"}]
        assert result.profile_arn == "arn:aws:p"
        assert source.profile_calls == 0

    async def test_profile_arn_is_resolved_when_absent(self):
        source = _FakeCatalogSource(
            profiles=[{"arn": "arn:aws:resuelto"}], models=[{"modelId": "auto"}]
        )
        catalog = ModelCatalog(source, _FakeClock(), ttl_seconds=300)

        result = await catalog.fetch({"access_token": "t"})

        assert result.profile_arn == "arn:aws:resuelto"
        assert source.profile_calls == 1

    async def test_second_call_within_ttl_uses_the_cache(self):
        source = _FakeCatalogSource(models=[{"modelId": "auto"}])
        clock = _FakeClock()
        catalog = ModelCatalog(source, clock, ttl_seconds=300)

        await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})
        clock.advance(100)
        await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})

        assert source.model_calls == 1

    async def test_cache_expires_after_the_ttl(self):
        source = _FakeCatalogSource(models=[{"modelId": "auto"}])
        clock = _FakeClock()
        catalog = ModelCatalog(source, clock, ttl_seconds=300)

        await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})
        clock.advance(301)
        await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})

        assert source.model_calls == 2

    async def test_without_profile_arn_returns_the_stale_cache(self):
        catalog = ModelCatalog(_FakeCatalogSource(), _FakeClock(), ttl_seconds=300)
        result = await catalog.fetch({"access_token": "t"})
        assert result.models == []

    async def test_empty_response_does_not_overwrite_the_cache(self):
        source = _FakeCatalogSource(models=[{"modelId": "auto"}])
        clock = _FakeClock()
        catalog = ModelCatalog(source, clock, ttl_seconds=300)
        await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})

        clock.advance(301)
        source.models = []  # el plano de gestión falla en la segunda llamada
        result = await catalog.fetch({"access_token": "t", "profile_arn": "arn:aws:p"})

        assert result.models == [{"modelId": "auto"}]

    async def test_independent_instances_do_not_share_cache(self):
        # Antes, _model_cache era del modulo: una instancia contaminaba a todas.
        source_a = _FakeCatalogSource(models=[{"modelId": "a"}])
        source_b = _FakeCatalogSource(models=[{"modelId": "b"}])
        clock = _FakeClock()

        result_a = await ModelCatalog(source_a, clock, 300).fetch(
            {"access_token": "t", "profile_arn": "arn:aws:p"}
        )
        result_b = await ModelCatalog(source_b, clock, 300).fetch(
            {"access_token": "t", "profile_arn": "arn:aws:p"}
        )

        assert result_a.models == [{"modelId": "a"}]
        assert result_b.models == [{"modelId": "b"}]
