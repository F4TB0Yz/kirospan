"""Decodificador del AWS EventStream binario.

El runtime de Kiro responde con `application/vnd.amazon.eventstream`: una
secuencia de frames con prelude de 12 bytes (longitud total, longitud de
cabeceras y CRC del prelude), cabeceras tipadas, payload y CRC final. Aquí solo
interesa el payload JSON, así que los CRC se descartan en lugar de verificarse.
"""

import json
import struct
from typing import AsyncIterator, Iterator

PRELUDE_SIZE = 12
MESSAGE_CRC_SIZE = 4

_HEADER_TYPE_BYTE = 6
_HEADER_TYPE_STRING = 7
_HEADER_TYPE_BINARY = 8


def _skip_headers(message: bytes, header_length: int) -> None:
    """Recorre el bloque de cabeceras sin extraer nada.

    El tipo de evento viaja en `:event-type`, pero el runtime no lo rellena de
    forma fiable, así que la clasificación se hace por la forma del payload en
    `protocols.kiro.event_decoder`.
    """
    position = PRELUDE_SIZE
    end = PRELUDE_SIZE + header_length
    limit = len(message)

    while position < end and position + 1 <= limit:
        name_length = message[position]
        position += 1 + name_length
        if position >= limit:
            return

        header_type = message[position]
        position += 1

        if header_type == _HEADER_TYPE_STRING:
            if position + 2 > limit:
                return
            position += 2 + struct.unpack_from(">H", message, position)[0]
        elif header_type == _HEADER_TYPE_BINARY:
            if position + 2 > limit:
                return
            position += 2 + struct.unpack_from(">H", message, position)[0]
        elif header_type == _HEADER_TYPE_BYTE:
            position += 1
        else:
            position += 1


def _payload_of(message: bytes) -> bytes:
    header_length = struct.unpack_from(">I", message, 4)[0]
    _skip_headers(message, header_length)
    payload = message[PRELUDE_SIZE + header_length : len(message) - MESSAGE_CRC_SIZE]
    return payload.rstrip(b"\x00 ")


def decode_frames(buffer: bytearray) -> Iterator[dict]:
    """Consume del buffer los frames completos y emite sus payloads JSON.

    Muta `buffer`: elimina lo consumido y deja el resto para el siguiente chunk.
    """
    while len(buffer) >= PRELUDE_SIZE:
        total_length = struct.unpack_from(">I", buffer, 0)[0]
        if total_length < PRELUDE_SIZE + MESSAGE_CRC_SIZE or len(buffer) < total_length:
            return

        message = bytes(buffer[:total_length])
        del buffer[:total_length]

        payload = _payload_of(message)
        if not payload:
            continue
        try:
            yield json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue


async def read_aws_eventstream(response) -> AsyncIterator[dict]:
    """Adapta una respuesta HTTP en streaming a un flujo de payloads JSON."""
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        for event in decode_frames(buffer):
            yield event
