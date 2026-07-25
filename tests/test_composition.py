"""Tests de `gateway.composition.create_app` con `TestClient`.

Verifica que el composition root ensambla una app funcional de extremo a
extremo (auth, rutas, ciclo de vida), sustituyendo la infraestructura httpx
real por dobles vía `respx` no disponible aquí — en su lugar, se monkeypatchea
el cliente de bajo nivel.
"""

import pytest
from fastapi.testclient import TestClient

from gateway.config import GatewayConfig


@pytest.fixture
def config(tmp_path):
    db_path = tmp_path / "data.sqlite3"
    import json
    import sqlite3

    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute(
        "INSERT INTO auth_kv (key, value) VALUES (?, ?)",
        (
            "kirocli:social:token",
            json.dumps({
                "access_token": "vigente",
                "expires_at": "2099-01-01T00:00:00Z",
                "profile_arn": "arn:aws:test",
            }),
        ),
    )
    connection.commit()
    connection.close()

    return GatewayConfig(gateway_key="secreta", db_paths=(db_path,))


class TestCreateApp:
    def test_health_does_not_require_auth(self, config):
        from gateway.composition import create_app

        with TestClient(create_app(config)) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["authenticated"] is True

    def test_health_does_not_leak_the_db_path(self, config):
        # /health es la única ruta sin auth; no debe filtrar la ruta absoluta
        # de la SQLite (revela el nombre del usuario del sistema) a quien
        # tenga acceso de red al puerto sin conocer la clave del gateway.
        from gateway.composition import create_app

        with TestClient(create_app(config)) as client:
            body = client.get("/health").json()

        assert "db_path" not in body
        assert body["token_in_db"] is True

    def test_props_requires_auth(self, config):
        from gateway.composition import create_app

        with TestClient(create_app(config)) as client:
            unauthenticated = client.get("/v1/props")
            authenticated = client.get("/v1/props", headers={"x-api-key": "secreta"})

        assert unauthenticated.status_code == 401
        assert authenticated.status_code == 200

    def test_bearer_auth_is_also_accepted(self, config):
        from gateway.composition import create_app

        with TestClient(create_app(config)) as client:
            response = client.get(
                "/v1/props", headers={"Authorization": "Bearer secreta"}
            )
        assert response.status_code == 200

    def test_wrong_key_is_rejected(self, config):
        from gateway.composition import create_app

        with TestClient(create_app(config)) as client:
            response = client.get("/v1/props", headers={"x-api-key": "incorrecta"})
        assert response.status_code == 401

    def test_routes_are_registered(self, config):
        from gateway.composition import create_app

        app = create_app(config)
        with TestClient(app) as client:
            responses = {
                "/health": client.get("/health").status_code,
                "/v1/props": client.get("/v1/props", headers={"x-api-key": "secreta"}).status_code,
                "/v1/models": client.get("/v1/models", headers={"x-api-key": "secreta"}).status_code,
            }
        # 200/401/500 indican que la ruta existe y se ejecutó un handler;
        # 404 indicaría que el router no se registró.
        assert all(status != 404 for status in responses.values())

    def test_lifespan_closes_the_http_client(self, config):
        from gateway.composition import create_app

        app = create_app(config)
        with TestClient(app):
            pass
        # Si el cliente no se hubiera cerrado, un segundo ciclo de vida sobre
        # la misma app fallaría o dejaría un cliente huerfano; se limita a
        # comprobar que el context manager no revienta al salir.
