"""Traducción HTTP de errores del runtime en rutas de chat."""

import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.adapters.inbound.http.routers.anthropic import build_anthropic_router
from gateway.adapters.inbound.http.routers.chat import build_chat_router
from gateway.domain.errors import UpstreamError


class _TokenService:
    async def ensure(self):
        return {
            "access_token": "token",
            "profile_arn": "arn:aws:test",
        }


class _FailingRuntimeClient:
    def __init__(self, error):
        self._error = error

    def stream(self, *, access_token, state):
        async def generator():
            raise self._error
            yield  # pragma: no cover - convierte la función en generador

        return generator()


def _client(error):
    app = FastAPI()
    runtime_client_factory = lambda: _FailingRuntimeClient(error)
    app.include_router(build_chat_router(_TokenService(), None, runtime_client_factory))
    app.include_router(
        build_anthropic_router(_TokenService(), None, runtime_client_factory)
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("status_code", "expected_status"),
    [(504, 504), (403, 403), (None, 502)],
)
def test_openai_non_stream_preserves_upstream_status(status_code, expected_status):
    with _client(UpstreamError("runtime caído", status_code=status_code)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={"model": "claude-sonnet-4", "messages": [{"role": "user", "content": "hola"}]},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": "runtime caído"}


def test_anthropic_non_stream_returns_upstream_error():
    with _client(UpstreamError("runtime caído", status_code=502)) as client:
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-sonnet-4",
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "hola"}],
            },
        )

    assert response.status_code == 502
    assert response.json() == {"detail": "runtime caído"}


def test_openai_stream_serializes_error_and_closes_sse():
    with _client(UpstreamError("runtime caído", status_code=502)) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-sonnet-4",
                "messages": [{"role": "user", "content": "hola"}],
                "stream": True,
            },
        )

    assert response.status_code == 200
    first_line = response.text.splitlines()[0]
    first_chunk = json.loads(first_line.removeprefix("data: "))
    assert first_chunk["choices"][0]["delta"]["content"] == (
        "[Kiro error: runtime caído]"
    )
    assert response.text.endswith("data: [DONE]\n\n")
