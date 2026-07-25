"""Utilidades compartidas por los tests de regresión.

`normalize` sustituye los valores no deterministas (uuid4, epochs) por
marcadores estables, de modo que los snapshots puedan compararse byte a byte
entre ejecuciones. `FakeRuntimeClient` reproduce la interfaz mínima de
`httpx.AsyncClient` que consumen los runtimes actuales.
"""

import re

_UUID_HEX_RE = re.compile(r"^[0-9a-f]{12,32}$")
_ID_PREFIXES = ("chatcmpl-", "msg_", "call_", "toolu_")

VOLATILE_FIELDS = {"conversationId", "created"}


def normalize(value):
    """Reemplaza identificadores y timestamps generados al azar."""
    if isinstance(value, dict):
        return {key: _normalize_field(key, item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, str):
        return _normalize_string(value)
    return value


def _normalize_field(key, value):
    if key in VOLATILE_FIELDS:
        return f"<{key}>"
    return normalize(value)


def _normalize_string(text):
    for prefix in _ID_PREFIXES:
        if text.startswith(prefix) and _UUID_HEX_RE.match(text[len(prefix):]):
            return f"{prefix}<generated>"
    if _UUID_HEX_RE.match(text.replace("-", "")) and len(text) == 36:
        return "<uuid>"
    return text


class _FakeResponse:
    def __init__(self, status_code, body=b""):
        self.status_code = status_code
        self._body = body

    async def aread(self):
        return self._body


class _FakeStreamContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeRuntimeClient:
    """Cliente que devuelve siempre la misma respuesta y registra las llamadas."""

    def __init__(self, status_code=200, body=b""):
        self._response = _FakeResponse(status_code, body)
        self.calls = []

    def stream(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _FakeStreamContext(self._response)


def event_reader(events):
    """Construye un sustituto de `read_aws_eventstream` con eventos fijos."""

    async def read(_response):
        for event in events:
            yield event

    return read
