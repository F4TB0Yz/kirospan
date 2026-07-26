"""Decodificador del AWS EventStream binario.

El runtime de Kiro responde con `application/vnd.amazon.eventstream`: una
secuencia de frames con prelude de 12 bytes (longitud total, longitud de
cabeceras y CRC del prelude), cabeceras tipadas, payload y CRC final. Aquí solo
interesa el payload JSON, así que los CRC se descartan en lugar de verificarse.
"""

import json
import struct
from typing import AsyncIterator, Iterator, NamedTuple

PRELUDE_SIZE = 12
MESSAGE_CRC_SIZE = 4

_HEADER_TYPE_BYTE = 6
_HEADER_TYPE_STRING = 7
_HEADER_TYPE_BINARY = 8

EVENTSTREAM_EXCEPTION_KEY = "_aws_eventstream_exception"


class TruncatedEventStreamError(Exception):
    """La respuesta terminó antes de completar un frame AWS EventStream."""


class KiroFrame(NamedTuple):
    """Payload Kiro junto al tipo declarado por AWS EventStream."""

    event_type: str
    payload: dict


def _headers_of(message: bytes, header_length: int) -> dict[str, object]:
    """Extrae las cabeceras soportadas del frame sin interpretar el payload."""
    headers: dict[str, object] = {}
    position = PRELUDE_SIZE
    end = min(PRELUDE_SIZE + header_length, len(message))

    while position < end:
        name_length = message[position]
        position += 1
        if position + name_length + 1 > end:
            break
        name = message[position : position + name_length].decode("utf-8", errors="replace")
        position += name_length
        header_type = message[position]
        position += 1

        if header_type in {_HEADER_TYPE_STRING, _HEADER_TYPE_BINARY}:
            if position + 2 > end:
                break
            value_length = struct.unpack_from(">H", message, position)[0]
            position += 2
            if position + value_length > end:
                break
            raw_value = message[position : position + value_length]
            position += value_length
            headers[name] = (
                raw_value.decode("utf-8", errors="replace")
                if header_type == _HEADER_TYPE_STRING
                else raw_value
            )
        elif header_type == _HEADER_TYPE_BYTE:
            if position >= end:
                break
            headers[name] = message[position]
            position += 1
        else:
            # Los frames de Kiro observados solo usan byte, string y binary.
            # Ante un tipo desconocido no es seguro inferir su longitud.
            break

    return headers


def _payload_of(message: bytes, header_length: int) -> bytes:
    payload = message[PRELUDE_SIZE + header_length : len(message) - MESSAGE_CRC_SIZE]
    return payload.rstrip(b"\x00 ")


def decode_kiro_frames(buffer: bytearray) -> Iterator[KiroFrame]:
    """Consume frames completos conservando el header `:event-type`."""
    while len(buffer) >= PRELUDE_SIZE:
        total_length = struct.unpack_from(">I", buffer, 0)[0]
        if total_length < PRELUDE_SIZE + MESSAGE_CRC_SIZE or len(buffer) < total_length:
            return

        message = bytes(buffer[:total_length])
        del buffer[:total_length]

        header_length = struct.unpack_from(">I", message, 4)[0]
        headers = _headers_of(message, header_length)
        payload = _payload_of(message, header_length)
        if not payload:
            continue
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        event_type = str(headers.get(":event-type") or "")
        if headers.get(":message-type") == "exception":
            message_text = (
                parsed.get("message") or parsed.get("Message") or "Unknown Kiro stream error"
                if isinstance(parsed, dict)
                else "Unknown Kiro stream error"
            )
            yield KiroFrame(
                event_type,
                {
                    EVENTSTREAM_EXCEPTION_KEY: {
                        "exception_type": str(headers.get(":exception-type") or "unknown"),
                        "message": str(message_text),
                    }
                },
            )
            continue

        if isinstance(parsed, dict):
            yield KiroFrame(event_type, parsed)


def decode_frames(buffer: bytearray) -> Iterator[dict]:
    """Consume frames completos conservando el contrato legacy de payloads."""
    for frame in decode_kiro_frames(buffer):
        yield frame.payload


async def read_kiro_frames(response) -> AsyncIterator[KiroFrame]:
    """Adapta una respuesta HTTP a frames Kiro con tipo de evento."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        for frame in decode_kiro_frames(buffer):
            yield frame
    if buffer:
        raise TruncatedEventStreamError(
            f"EventStream truncado con {len(buffer)} bytes pendientes"
        )


async def read_aws_eventstream(response) -> AsyncIterator[dict]:
    """Adapta una respuesta HTTP conservando el contrato legacy de payloads."""
    async for frame in read_kiro_frames(response):
        yield frame.payload
