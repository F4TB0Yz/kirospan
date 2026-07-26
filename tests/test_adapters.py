"""Tests de los adaptadores de salida.

El decodificador de EventStream se prueba con frames binarios construidos a
mano: es la pieza más frágil del gateway y hasta ahora solo estaba cubierta
indirectamente, parcheándola en los tests legacy.
"""

import json
import struct
import zlib

import pytest

from gateway.adapters.outbound.aws_eventstream import (
    EVENTSTREAM_EXCEPTION_KEY,
    decode_frames,
    decode_kiro_frames,
    read_aws_eventstream,
)
from gateway.adapters.outbound.clock import SystemClock
from gateway.adapters.outbound.kiro_auth import DesktopTokenRefresher
from gateway.adapters.outbound.kiro_models import HttpModelCatalogSource
from gateway.adapters.outbound.kiro_runtime import RUNTIME_HEADERS, HttpKiroRuntimeClient
from gateway.adapters.outbound.sqlite_tokens import TOKEN_KEY, SqliteTokenRepository
from gateway.config import GatewayConfig
from gateway.domain.errors import AuthError, UpstreamError

CONFIG = GatewayConfig(gateway_key="k", region="us-east-1")


# ── Constructor de frames ───────────────────────────────────────────────────

def _string_header(name: str, value: str) -> bytes:
    encoded_name = name.encode()
    encoded_value = value.encode()
    return (
        bytes([len(encoded_name)])
        + encoded_name
        + bytes([7])
        + struct.pack(">H", len(encoded_value))
        + encoded_value
    )


def build_frame(
    payload: dict,
    event_type: str = "assistantResponseEvent",
    *,
    message_type: str = "event",
    exception_type: str | None = None,
) -> bytes:
    """Construye un frame de AWS EventStream válido, con CRC incluidos."""
    body = json.dumps(payload).encode()
    headers = _string_header(":message-type", message_type)
    if event_type:
        headers += _string_header(":event-type", event_type)
    if exception_type:
        headers += _string_header(":exception-type", exception_type)
    headers += _string_header(":content-type", "application/json")
    total_length = 12 + len(headers) + len(body) + 4
    prelude = struct.pack(">II", total_length, len(headers))
    prelude += struct.pack(">I", zlib.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack(">I", zlib.crc32(message) & 0xFFFFFFFF)


class _FakeStreamResponse:
    """Respuesta que entrega los bytes en los trozos indicados."""

    def __init__(self, chunks, status_code=200, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk

    async def aread(self):
        return b"".join(self._chunks)


class TestDecodeFrames:
    def test_typed_decoder_preserves_event_type(self):
        frame = build_frame(
            {"text": "plan", "signature": "firma"},
            event_type="reasoningContentEvent",
        )
        decoded = list(decode_kiro_frames(bytearray(frame)))
        assert decoded[0].event_type == "reasoningContentEvent"
        assert decoded[0].payload == {"text": "plan", "signature": "firma"}

    def test_single_frame(self):
        buffer = bytearray(build_frame({"content": "hola"}))
        assert list(decode_frames(buffer)) == [{"content": "hola"}]
        assert buffer == bytearray()

    def test_several_frames_in_one_buffer(self):
        buffer = bytearray(
            build_frame({"content": "a"}) + build_frame({"content": "b"})
        )
        assert list(decode_frames(buffer)) == [{"content": "a"}, {"content": "b"}]

    def test_incomplete_frame_stays_in_the_buffer(self):
        frame = build_frame({"content": "hola"})
        buffer = bytearray(frame[:-6])
        assert list(decode_frames(buffer)) == []
        assert len(buffer) == len(frame) - 6

        # Al llegar el resto, el frame se decodifica.
        buffer.extend(frame[-6:])
        assert list(decode_frames(buffer)) == [{"content": "hola"}]

    def test_prelude_shorter_than_twelve_bytes_waits(self):
        buffer = bytearray(b"\x00\x00")
        assert list(decode_frames(buffer)) == []
        assert len(buffer) == 2

    def test_absurd_total_length_is_not_consumed(self):
        # Longitud menor que el propio prelude: frame corrupto, no se consume.
        buffer = bytearray(struct.pack(">II", 4, 0) + b"\x00" * 8)
        assert list(decode_frames(buffer)) == []

    def test_invalid_json_payload_is_skipped(self):
        body = b"{no es json"
        headers = _string_header(":event-type", "x")
        total = 12 + len(headers) + len(body) + 4
        prelude = struct.pack(">II", total, len(headers))
        prelude += struct.pack(">I", 0)
        frame = prelude + headers + body + struct.pack(">I", 0)

        buffer = bytearray(frame + build_frame({"content": "valido"}))
        assert list(decode_frames(buffer)) == [{"content": "valido"}]

    def test_eventstream_exception_preserves_type_and_message(self):
        frame = build_frame(
            {"message": "Encountered an unexpected error when processing the request, please try again."},
            event_type="",
            message_type="exception",
            exception_type="error",
        )

        assert list(decode_frames(bytearray(frame))) == [
            {
                EVENTSTREAM_EXCEPTION_KEY: {
                    "exception_type": "error",
                    "message": (
                        "Encountered an unexpected error when processing the request, "
                        "please try again."
                    ),
                }
            }
        ]

    def test_binary_and_byte_headers_are_skipped(self):
        body = json.dumps({"content": "x"}).encode()
        headers = (
            bytes([3]) + b"bin" + bytes([8]) + struct.pack(">H", 2) + b"\x01\x02"
            + bytes([2]) + b"by" + bytes([6]) + b"\x01"
        )
        total = 12 + len(headers) + len(body) + 4
        prelude = struct.pack(">II", total, len(headers)) + struct.pack(">I", 0)
        frame = prelude + headers + body + struct.pack(">I", 0)

        assert list(decode_frames(bytearray(frame))) == [{"content": "x"}]


class TestReadAwsEventstream:
    async def test_events_arrive_across_chunk_boundaries(self):
        payload = build_frame({"content": "uno"}) + build_frame({"content": "dos"})
        # Trozos que cortan en mitad de un frame.
        chunks = [payload[:5], payload[5:30], payload[30:]]
        response = _FakeStreamResponse(chunks)

        events = [event async for event in read_aws_eventstream(response)]
        assert events == [{"content": "uno"}, {"content": "dos"}]

    async def test_empty_response_yields_nothing(self):
        assert [event async for event in read_aws_eventstream(_FakeStreamResponse([]))] == []


# ── Repositorio de tokens ───────────────────────────────────────────────────

@pytest.fixture
def sqlite_db(tmp_path):
    import sqlite3

    path = tmp_path / "data.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE auth_kv (key TEXT PRIMARY KEY, value TEXT)")
    connection.execute(
        "INSERT INTO auth_kv (key, value) VALUES (?, ?)",
        (TOKEN_KEY, json.dumps({"access_token": "x" * 60})),
    )
    connection.commit()
    connection.close()
    return path


def _no_keychain(monkeypatch):
    """Evita que los tests dependan de si la máquina tiene un Keychain real."""
    import gateway.adapters.outbound.sqlite_tokens as module

    monkeypatch.setattr(module, "_read_keychain_token", lambda: None)


class TestSqliteTokenRepository:
    async def test_load_reads_the_token(self, sqlite_db, monkeypatch):
        _no_keychain(monkeypatch)
        repository = SqliteTokenRepository((sqlite_db,))
        assert await repository.load() == {"access_token": "x" * 60}

    async def test_save_then_load_round_trip(self, sqlite_db, monkeypatch):
        _no_keychain(monkeypatch)
        repository = SqliteTokenRepository((sqlite_db,))
        await repository.save({"access_token": "y" * 60, "expires_at": "2099-01-01T00:00:00Z"})
        assert (await repository.load())["access_token"] == "y" * 60

    def test_save_inserts_token_when_row_is_missing(self, sqlite_db):
        import sqlite3

        connection = sqlite3.connect(sqlite_db)
        connection.execute("DELETE FROM auth_kv WHERE key = ?", (TOKEN_KEY,))
        connection.commit()
        connection.close()

        token = {"access_token": "nuevo", "expires_at": "2099-01-01T00:00:00Z"}
        SqliteTokenRepository((sqlite_db,)).save_sync(token)

        connection = sqlite3.connect(sqlite_db)
        rows = connection.execute(
            "SELECT value FROM auth_kv WHERE key = ?", (TOKEN_KEY,)
        ).fetchall()
        connection.close()
        assert len(rows) == 1
        assert json.loads(rows[0][0]) == token

    def test_save_updates_existing_token_without_duplicating_row(self, sqlite_db):
        import sqlite3

        token = {"access_token": "actualizado", "expires_at": "2099-01-01T00:00:00Z"}
        SqliteTokenRepository((sqlite_db,)).save_sync(token)

        connection = sqlite3.connect(sqlite_db)
        rows = connection.execute(
            "SELECT value FROM auth_kv WHERE key = ?", (TOKEN_KEY,)
        ).fetchall()
        connection.close()
        assert len(rows) == 1
        assert json.loads(rows[0][0]) == token

    async def test_missing_database_falls_back_to_keychain(self, tmp_path, monkeypatch):
        _no_keychain(monkeypatch)
        repository = SqliteTokenRepository((tmp_path / "no-existe.sqlite3",))
        assert await repository.load() is None
        assert repository.location() is None

    async def test_save_without_database_raises(self, tmp_path):
        repository = SqliteTokenRepository((tmp_path / "no-existe.sqlite3",))
        with pytest.raises(RuntimeError, match="DB not found"):
            await repository.save({"access_token": "x"})

    def test_first_existing_path_wins(self, sqlite_db, tmp_path):
        repository = SqliteTokenRepository((tmp_path / "ausente.sqlite3", sqlite_db))
        assert repository.location() == str(sqlite_db)

    async def test_corrupt_json_falls_back_to_keychain(self, sqlite_db, monkeypatch):
        import sqlite3

        _no_keychain(monkeypatch)
        connection = sqlite3.connect(sqlite_db)
        connection.execute("UPDATE auth_kv SET value = ? WHERE key = ?", ("{roto", TOKEN_KEY))
        connection.commit()
        connection.close()

        assert await SqliteTokenRepository((sqlite_db,)).load() is None

    async def test_short_placeholder_token_falls_back_to_keychain(self, sqlite_db, monkeypatch):
        import gateway.adapters.outbound.sqlite_tokens as module

        keychain_token = {"access_token": "z" * 60}
        monkeypatch.setattr(module, "_read_keychain_token", lambda: keychain_token)

        connection = __import__("sqlite3").connect(sqlite_db)
        connection.execute(
            "UPDATE auth_kv SET value = ? WHERE key = ?",
            (json.dumps({"access_token": "corto"}), TOKEN_KEY),
        )
        connection.commit()
        connection.close()

        loaded = await SqliteTokenRepository((sqlite_db,)).load()
        assert loaded == keychain_token

    async def test_keychain_token_is_synced_back_to_sqlite(self, sqlite_db, monkeypatch):
        import gateway.adapters.outbound.sqlite_tokens as module

        keychain_token = {"access_token": "w" * 60}
        monkeypatch.setattr(module, "_read_keychain_token", lambda: keychain_token)

        connection = __import__("sqlite3").connect(sqlite_db)
        connection.execute(
            "UPDATE auth_kv SET value = ? WHERE key = ?",
            (json.dumps({"access_token": "corto"}), TOKEN_KEY),
        )
        connection.commit()
        connection.close()

        repository = SqliteTokenRepository((sqlite_db,))
        await repository.load()

        _no_keychain(monkeypatch)
        assert await repository.load() == keychain_token

    async def test_placeholder_without_keychain_returns_the_placeholder(self, sqlite_db, monkeypatch):
        _no_keychain(monkeypatch)
        connection = __import__("sqlite3").connect(sqlite_db)
        connection.execute(
            "UPDATE auth_kv SET value = ? WHERE key = ?",
            (json.dumps({"access_token": "corto"}), TOKEN_KEY),
        )
        connection.commit()
        connection.close()

        loaded = await SqliteTokenRepository((sqlite_db,)).load()
        assert loaded == {"access_token": "corto"}


class TestReadKeychainToken:
    def test_missing_security_binary_returns_none(self, monkeypatch):
        import gateway.adapters.outbound.sqlite_tokens as module

        def raise_missing(*args, **kwargs):
            raise FileNotFoundError("no security binary")

        monkeypatch.setattr(module.subprocess, "run", raise_missing)
        assert module._read_keychain_token() is None

    def test_nonzero_exit_returns_none(self, monkeypatch):
        import gateway.adapters.outbound.sqlite_tokens as module

        class _Result:
            returncode = 1
            stdout = ""

        monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: _Result())
        assert module._read_keychain_token() is None

    def test_valid_output_is_parsed(self, monkeypatch):
        import gateway.adapters.outbound.sqlite_tokens as module

        class _Result:
            returncode = 0
            stdout = json.dumps({"access_token": "del-keychain"})

        monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: _Result())
        assert module._read_keychain_token() == {"access_token": "del-keychain"}

    def test_invalid_json_output_returns_none(self, monkeypatch):
        import gateway.adapters.outbound.sqlite_tokens as module

        class _Result:
            returncode = 0
            stdout = "no es json"

        monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: _Result())
        assert module._read_keychain_token() is None


# ── Refresco de token ───────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class _RecordingClient:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


class TestDesktopTokenRefresher:
    async def test_successful_refresh_updates_the_token(self):
        client = _RecordingClient(
            _FakeResponse(payload={"accessToken": "fresco", "expiresIn": 900})
        )
        refresher = DesktopTokenRefresher(client, CONFIG)

        refreshed = await refresher.refresh({"refresh_token": "r1", "otro": "campo"})

        assert refreshed["access_token"] == "fresco"
        assert refreshed["refresh_token"] == "r1"
        assert refreshed["expires_at"].endswith("Z")
        assert refreshed["region"] == "us-east-1"
        # Los campos no relacionados se conservan.
        assert refreshed["otro"] == "campo"

    async def test_rotated_refresh_token_is_stored(self):
        client = _RecordingClient(
            _FakeResponse(payload={"accessToken": "a", "refreshToken": "r2"})
        )
        refreshed = await DesktopTokenRefresher(client, CONFIG).refresh({"refresh_token": "r1"})
        assert refreshed["refresh_token"] == "r2"

    async def test_profile_arn_from_the_response_is_kept(self):
        client = _RecordingClient(
            _FakeResponse(payload={"accessToken": "a", "profileArn": "arn:aws:x"})
        )
        refreshed = await DesktopTokenRefresher(client, CONFIG).refresh({"refresh_token": "r"})
        assert refreshed["profile_arn"] == "arn:aws:x"

    async def test_token_region_takes_precedence_in_the_url(self):
        client = _RecordingClient(_FakeResponse(payload={"accessToken": "a"}))
        await DesktopTokenRefresher(client, CONFIG).refresh(
            {"refresh_token": "r", "region": "eu-west-1"}
        )
        assert "eu-west-1" in client.calls[0][0]

    async def test_missing_refresh_token_is_rejected(self):
        refresher = DesktopTokenRefresher(_RecordingClient(_FakeResponse()), CONFIG)
        with pytest.raises(AuthError, match="refresh token not found"):
            await refresher.refresh({})

    async def test_http_error_is_reported(self):
        client = _RecordingClient(_FakeResponse(status_code=401))
        with pytest.raises(AuthError, match="HTTP 401"):
            await DesktopTokenRefresher(client, CONFIG).refresh({"refresh_token": "r"})

    async def test_response_without_access_token_is_rejected(self):
        client = _RecordingClient(_FakeResponse(payload={}))
        with pytest.raises(AuthError, match="did not include accessToken"):
            await DesktopTokenRefresher(client, CONFIG).refresh({"refresh_token": "r"})


# ── Cliente del runtime ─────────────────────────────────────────────────────

class _StreamContext:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _StreamingClient:
    """Devuelve una respuesta distinta por llamada y registra los headers."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return _StreamContext(self._responses.pop(0))


class _StubTokenService:
    def __init__(self, token):
        self._token = token
        self.calls = []

    async def ensure(self, *, force_refresh=False, rejected_access_token=None):
        self.calls.append({"force_refresh": force_refresh, "rejected": rejected_access_token})
        return self._token


class TestHttpKiroRuntimeClient:
    async def test_reasoning_frame_is_wrapped_for_domain_decoder(self):
        response = _FakeStreamResponse([
            build_frame(
                {"text": "plan", "signature": "firma"},
                event_type="reasoningContentEvent",
            )
        ])
        runtime = HttpKiroRuntimeClient(_StreamingClient([response]), CONFIG)

        events = [
            event
            async for event in runtime.stream(access_token="t", state={})
        ]

        assert events == [{
            "reasoningContent": {"text": "plan", "signature": "firma"}
        }]

    async def test_reasoning_payload_is_detected_without_event_type_header(self):
        response = _FakeStreamResponse([
            build_frame({"text": "plan", "signature": "firma"}, event_type="")
        ])
        runtime = HttpKiroRuntimeClient(_StreamingClient([response]), CONFIG)

        events = [
            event
            async for event in runtime.stream(access_token="t", state={})
        ]

        assert events == [{"text": "plan", "signature": "firma"}]

    async def test_successful_stream_yields_events(self):
        response = _FakeStreamResponse([build_frame({"content": "hola"})])
        client = _StreamingClient([response])
        runtime = HttpKiroRuntimeClient(client, CONFIG)

        events = [
            event
            async for event in runtime.stream(access_token="t", state={"conversationState": {}})
        ]
        assert events == [{"content": "hola"}]

    async def test_eventstream_exception_becomes_retryable_upstream_error(self):
        response = _FakeStreamResponse(
            [
                build_frame(
                    {
                        "message": (
                            "Encountered an unexpected error when processing the request, "
                            "please try again."
                        )
                    },
                    event_type="",
                    message_type="exception",
                    exception_type="error",
                )
            ],
            headers={"x-amzn-requestid": "request-123"},
        )
        runtime = HttpKiroRuntimeClient(_StreamingClient([response]), CONFIG)

        with pytest.raises(UpstreamError) as error:
            [event async for event in runtime.stream(access_token="t", state={})]

        assert error.value.status_code == 502
        assert error.value.retryable is True
        assert error.value.request_id == "request-123"
        assert "unexpected error" in str(error.value)

    async def test_non_transient_eventstream_exception_is_not_retryable(self):
        response = _FakeStreamResponse(
            [
                build_frame(
                    {"message": "Conversation payload is invalid; please try again"},
                    event_type="",
                    message_type="exception",
                    exception_type="ValidationException",
                )
            ]
        )
        runtime = HttpKiroRuntimeClient(_StreamingClient([response]), CONFIG)

        with pytest.raises(UpstreamError) as error:
            [event async for event in runtime.stream(access_token="t", state={})]

        assert error.value.retryable is False

    async def test_headers_are_sent_once_and_complete(self):
        client = _StreamingClient([_FakeStreamResponse([])])
        runtime = HttpKiroRuntimeClient(client, CONFIG)

        [event async for event in runtime.stream(access_token="tok", state={})]

        headers = client.calls[0]["headers"]
        assert headers["Authorization"] == "Bearer tok"
        for name, value in RUNTIME_HEADERS.items():
            assert headers[name] == value
        assert client.calls[0]["url"] == CONFIG.runtime_url

    async def test_non_403_error_raises_upstream_error(self):
        response = _FakeStreamResponse([b'{"message":"Invalid model ID"}'], status_code=400)
        runtime = HttpKiroRuntimeClient(_StreamingClient([response]), CONFIG)

        with pytest.raises(UpstreamError) as error:
            [event async for event in runtime.stream(access_token="t", state={})]
        assert error.value.status_code == 400
        assert "Invalid model ID" in str(error.value)

    async def test_403_refreshes_the_token_and_retries_once(self):
        rejected = _FakeStreamResponse([b"denegado"], status_code=403)
        accepted = _FakeStreamResponse([build_frame({"content": "tras refresco"})])
        client = _StreamingClient([rejected, accepted])
        token_service = _StubTokenService(
            {"access_token": "nuevo", "profile_arn": "arn:aws:nuevo"}
        )
        runtime = HttpKiroRuntimeClient(client, CONFIG, token_service)

        state = {"conversationState": {}}
        events = [event async for event in runtime.stream(access_token="viejo", state=state)]

        assert events == [{"content": "tras refresco"}]
        assert token_service.calls == [{"force_refresh": True, "rejected": "viejo"}]
        assert client.calls[1]["headers"]["Authorization"] == "Bearer nuevo"
        # El profileArn renovado se propaga al estado reenviado.
        assert state["profileArn"] == "arn:aws:nuevo"

    async def test_403_is_not_retried_twice(self):
        responses = [
            _FakeStreamResponse([b"denegado"], status_code=403),
            _FakeStreamResponse([b"denegado otra vez"], status_code=403),
        ]
        client = _StreamingClient(responses)
        runtime = HttpKiroRuntimeClient(client, CONFIG, _StubTokenService({"access_token": "n"}))

        with pytest.raises(UpstreamError):
            [event async for event in runtime.stream(access_token="viejo", state={})]
        assert len(client.calls) == 2

    async def test_403_without_token_service_is_an_upstream_error(self):
        client = _StreamingClient([_FakeStreamResponse([b"denegado"], status_code=403)])
        runtime = HttpKiroRuntimeClient(client, CONFIG)

        with pytest.raises(UpstreamError):
            [event async for event in runtime.stream(access_token="t", state={})]

    async def test_timeout_becomes_a_504(self):
        import httpx

        class _TimeoutClient:
            def stream(self, *args, **kwargs):
                raise httpx.TimeoutException("agotado")

        runtime = HttpKiroRuntimeClient(_TimeoutClient(), CONFIG)
        with pytest.raises(UpstreamError) as error:
            [event async for event in runtime.stream(access_token="t", state={})]
        assert error.value.status_code == 504


# ── Catálogo de modelos ─────────────────────────────────────────────────────

class _CatalogClient:
    def __init__(self, post_response=None, get_response=None):
        self._post_response = post_response
        self._get_response = get_response
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return self._post_response

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._get_response


class TestHttpModelCatalogSource:
    async def test_profiles_are_returned(self):
        client = _CatalogClient(
            post_response=_FakeResponse(payload={"profiles": [{"arn": "arn:aws:p"}]})
        )
        profiles = await HttpModelCatalogSource(client, CONFIG).list_profiles("t")
        assert profiles == [{"arn": "arn:aws:p"}]

    async def test_models_are_returned_with_the_profile_arn(self):
        client = _CatalogClient(
            get_response=_FakeResponse(payload={"models": [{"modelId": "auto"}]})
        )
        models = await HttpModelCatalogSource(client, CONFIG).list_models("t", "arn:aws:p")
        assert models == [{"modelId": "auto"}]
        assert client.calls[0][2]["params"]["profileArn"] == "arn:aws:p"

    async def test_http_error_yields_an_empty_list(self):
        client = _CatalogClient(post_response=_FakeResponse(status_code=500))
        assert await HttpModelCatalogSource(client, CONFIG).list_profiles("t") == []

    async def test_transport_failure_yields_an_empty_list(self):
        import httpx

        class _FailingClient:
            async def get(self, *args, **kwargs):
                raise httpx.ConnectError("sin red")

        source = HttpModelCatalogSource(_FailingClient(), CONFIG)
        assert await source.list_models("t", "arn") == []


class TestSystemClock:
    def test_now_advances(self):
        clock = SystemClock()
        assert clock.now() > 0
        assert clock.now() >= clock.now() - 1
