"""Tests unitarios del wrapper SSE keep-alive."""

import asyncio

import pytest

from gateway.adapters.inbound.http.sse_keepalive import (
    KEEP_ALIVE_COMMENT,
    sse_keep_alive,
)


async def _fast_source(*items):
    """Generador que emite items inmediatamente."""
    for item in items:
        yield item


async def _slow_source(items_with_delays):
    """Generador con delays configurables entre items.

    items_with_delays: lista de (item, delay_before_seconds)
    """
    for item, delay in items_with_delays:
        if delay > 0:
            await asyncio.sleep(delay)
        yield item


async def _collect(aiter):
    """Recolecta todos los items de un async iterator."""
    result = []
    async for item in aiter:
        result.append(item)
    return result


class TestSseKeepAlive:
    async def test_fast_source_passes_through_unchanged(self):
        """Si el source es rápido, no se emiten keep-alives."""
        items = ["data: chunk1\n\n", "data: chunk2\n\n", "data: [DONE]\n\n"]
        result = await _collect(sse_keep_alive(_fast_source(*items), interval=1.0))
        assert result == items

    async def test_slow_source_gets_keep_alive_pings(self):
        """Si el source tarda más que el interval, se intercalan comments."""
        items_with_delays = [
            ("data: first\n\n", 0),     # emitido inmediatamente
            ("data: second\n\n", 0.4),  # después de 0.4s (>0.2s interval)
        ]
        result = await _collect(
            sse_keep_alive(_slow_source(items_with_delays), interval=0.2)
        )
        # Debería haber al menos un keep-alive entre first y second
        assert "data: first\n\n" in result
        assert "data: second\n\n" in result
        assert KEEP_ALIVE_COMMENT in result
        # El keep-alive debe estar entre first y second
        first_idx = result.index("data: first\n\n")
        second_idx = result.index("data: second\n\n")
        ka_idx = result.index(KEEP_ALIVE_COMMENT)
        assert first_idx < ka_idx < second_idx

    async def test_multiple_keep_alives_during_long_silence(self):
        """Silencios largos producen múltiples pings."""
        items_with_delays = [
            ("data: start\n\n", 0),
            ("data: end\n\n", 0.7),  # 0.7s con interval=0.2s → ~3 pings
        ]
        result = await _collect(
            sse_keep_alive(_slow_source(items_with_delays), interval=0.2)
        )
        ka_count = result.count(KEEP_ALIVE_COMMENT)
        assert ka_count >= 2, f"Esperados >=2 keep-alives, got {ka_count}. Items: {result}"

    async def test_empty_source_no_items(self):
        """Un source vacío no produce nada (ni keep-alives)."""
        result = await _collect(sse_keep_alive(_fast_source(), interval=0.1))
        assert result == []

    async def test_source_exception_propagates(self):
        """Si el source lanza una excepción, se propaga al consumidor."""

        async def _failing_source():
            yield "data: ok\n\n"
            raise RuntimeError("upstream exploded")

        with pytest.raises(RuntimeError, match="upstream exploded"):
            await _collect(sse_keep_alive(_failing_source(), interval=0.1))

    async def test_keep_alive_comment_is_valid_sse(self):
        """El comment de keep-alive es un SSE comment válido."""
        assert KEEP_ALIVE_COMMENT.startswith(":")
        assert KEEP_ALIVE_COMMENT.endswith("\n\n")

    async def test_client_disconnect_closes_source_without_pending_tasks(self):
        """Si el consumidor cierra antes de tiempo, no quedan tareas pendientes."""
        closed = asyncio.Event()

        async def _endless():
            try:
                while True:
                    yield "data: x\n\n"
            finally:
                closed.set()

        stream = sse_keep_alive(_endless(), interval=1.0)
        assert await stream.__anext__() == "data: x\n\n"
        await stream.aclose()

        assert closed.is_set()
        pending = [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert pending == []

    async def test_preserves_order_of_items(self):
        """Los items se emiten en el mismo orden, sin reordenar."""
        items = [f"data: item_{i}\n\n" for i in range(10)]
        result = await _collect(sse_keep_alive(_fast_source(*items), interval=1.0))
        assert result == items

    async def test_initial_silence_produces_keep_alive(self):
        """Si el source tarda en emitir su primer item, se envían keep-alives."""
        items_with_delays = [
            ("data: delayed\n\n", 0.5),  # delay de 0.5s antes del primer item
        ]
        result = await _collect(
            sse_keep_alive(_slow_source(items_with_delays), interval=0.2)
        )
        # Debería haber keep-alives antes del primer item real
        assert KEEP_ALIVE_COMMENT in result
        assert result[-1] == "data: delayed\n\n"
        first_ka = result.index(KEEP_ALIVE_COMMENT)
        first_data = result.index("data: delayed\n\n")
        assert first_ka < first_data
