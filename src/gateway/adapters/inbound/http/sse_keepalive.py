"""Keep-alive para streams SSE: envía comments cuando el upstream tarda.

Cuando el modelo de Kiro piensa (reasoning extenso), puede tardar 30-120+
segundos sin emitir un solo byte. La conexión entre gateway y Kiro sigue
abierta, pero el cliente del gateway (Codex, Hermes, httpx, browsers) puede
cerrar por read timeout si no recibe datos. Este wrapper resuelve ese problema
emitiendo SSE comments (`: keep-alive\n\n`) periódicamente mientras el
generador upstream está en silencio.

Los SSE comments (líneas que empiezan con `:`) son ignorados por todos los
parsers SSE conformes (spec: https://html.spec.whatwg.org/multipage/server-sent-events.html),
así que no afectan el contenido que recibe el cliente.

Uso:
    StreamingResponse(
        sse_keep_alive(openai_stream(events, model), interval=15),
        media_type="text/event-stream",
    )
"""

import asyncio
import logging
import time
from typing import AsyncIterator

log = logging.getLogger("kirospan")

DEFAULT_KEEP_ALIVE_INTERVAL = 15  # segundos entre pings
KEEP_ALIVE_COMMENT = ": keep-alive\n\n"


async def sse_keep_alive(
    source: AsyncIterator[str],
    *,
    interval: float = DEFAULT_KEEP_ALIVE_INTERVAL,
) -> AsyncIterator[str]:
    """Envuelve un generador SSE y emite comments de keep-alive durante silencios.

    Implementación: usa `asyncio.wait` con timeout para esperar el próximo
    item del source. Si el timeout expira antes de que el source produzca,
    emite el comment y vuelve a esperar. Cuando el source produce, lo
    re-emite inmediatamente.

    El wrapper no modifica, filtra ni reordena los items del source: solo
    intercala comments durante los gaps.
    """
    # Envolver el async iterator en una queue para poder hacer await con timeout.
    # Usamos un sentinel para señalar el fin del iterador.
    _SENTINEL = object()
    _ERROR = object()

    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    error_holder: list = []

    async def _producer():
        """Lee del source y pone items en la queue.

        Si la tarea se cancela (el cliente cerró la conexión) no se intenta
        encolar el sentinel: con la queue llena ese `put` quedaría bloqueado
        para siempre y la tarea moriría pendiente.
        """
        try:
            async for item in source:
                await queue.put(item)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error_holder.append(exc)
            await queue.put(_ERROR)
            return
        await queue.put(_SENTINEL)

    producer_task = asyncio.ensure_future(_producer())
    keep_alive_count = 0

    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=interval)
            except asyncio.TimeoutError:
                # El source no ha producido en `interval` segundos — emitir keep-alive.
                keep_alive_count += 1
                if keep_alive_count == 1:
                    log.debug("SSE keep-alive: upstream silent, sending first ping")
                yield KEEP_ALIVE_COMMENT
                continue

            if item is _SENTINEL:
                break
            if item is _ERROR:
                if error_holder:
                    raise error_holder[0]
                break

            if keep_alive_count > 0:
                log.info(
                    "SSE keep-alive: upstream resumed after %d pings (%.0fs silent)",
                    keep_alive_count,
                    keep_alive_count * interval,
                )
                keep_alive_count = 0
            yield item
    finally:
        producer_task.cancel()
        await asyncio.gather(producer_task, return_exceptions=True)
        # Con el productor ya detenido, el source no está en ejecución y se
        # puede cerrar de forma determinista (libera el stream HTTP de Kiro).
        aclose = getattr(source, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:
                log.debug("SSE keep-alive: error closing source", exc_info=True)
