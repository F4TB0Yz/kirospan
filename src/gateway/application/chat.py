"""Caso de uso: obtener la respuesta del asistente como eventos de dominio.

Sustituye a las tres implementaciones que decodificaban el stream de Kiro por
separado (`_stream_runtime`, `_non_stream_runtime` y el reparseo interno de
`_anthropic_stream_runtime`). Aquí se decodifica una sola vez; los presenters
de cada protocolo consumen `AssistantEvent` y no vuelven a tocar el formato de
Kiro.

La acumulación de argumentos de una tool call sigue `active_tool_id`: el
`toolUseId` de la última `toolUse` vista, que es a quien pertenece un evento
`toolUseInput` posterior sin id propio. Es el criterio canónico — el monolito
tenía aquí una divergencia entre su ruta de streaming (`active_tool_id`) y su
ruta sin streaming (`list(tool_calls.keys())[-1]`), que coinciden salvo cuando
una respuesta trae dos o más tool calls; queda resuelta al unificarse en un
único productor.

Las tool calls en formato bracket (`[Called nombre with args: {...}]`) se
detectan evento a evento, no sobre el texto acumulado de todo el turno: Kiro
las emite completas dentro de un único evento `content`, así que limpiarlas
antes de emitir el `TextDelta` evita que el texto crudo llegue al cliente en
streaming. Si algún día el runtime las partiera entre dos eventos `content`,
esa fracción no se detectaría — es una limitación conocida, no cubierta por
los datos de producción disponibles al momento de esta corrección.
"""

import asyncio
import logging

from ..domain.errors import UpstreamError
from ..domain.events import (
    AssistantEvent,
    Completed,
    StreamError,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageReport,
)
from ..protocols.kiro.bracket_tools import parse_bracket_tool_calls
from ..protocols.kiro.event_decoder import parse_kiro_event

MAX_TRANSIENT_ATTEMPTS = 2
TRANSIENT_RETRY_DELAY_SECONDS = 0.15

log = logging.getLogger("kirospan")


def _model_id_of(state: dict) -> str:
    conversation = state.get("conversationState") or {}
    current = conversation.get("currentMessage") or {}
    user_input = current.get("userInputMessage") or {}
    return str(user_input.get("modelId") or "unknown")


class ChatService:
    """Traduce el stream de Kiro a `AssistantEvent`, una sola vez por turno."""

    def __init__(self, runtime_client, sleep=asyncio.sleep) -> None:
        self._runtime_client = runtime_client
        self._sleep = sleep

    async def run(self, *, access_token: str, state: dict):
        """Genera eventos y reintenta una excepción transitoria solo antes de emitir."""
        tool_indices: dict[str, int] = {}
        active_tool_id: str | None = None
        next_index = 0
        saw_tool_use = False
        emitted_committed_output = False
        thinking_events = 0
        thinking_chars = 0
        attempt = 1

        while True:
            pending_thinking: list[ThinkingDelta] = []
            try:
                async for raw_event in self._runtime_client.stream(
                    access_token=access_token, state=state
                ):
                    parsed = parse_kiro_event(raw_event)
                    if not parsed:
                        continue
                    event_type, data = parsed["type"], parsed["data"]

                    if event_type == "thinking":
                        text = data["text"]
                        thinking_events += 1
                        thinking_chars += len(text)
                        pending_thinking.append(
                            ThinkingDelta(text, data.get("signature", ""))
                        )

                    elif event_type == "content":
                        bracket_tools, cleaned_text = parse_bracket_tool_calls(data)
                        if cleaned_text:
                            for thinking in pending_thinking:
                                yield thinking
                            pending_thinking.clear()
                            emitted_committed_output = True
                            yield TextDelta(cleaned_text)
                        for bracket_tool in bracket_tools:
                            for thinking in pending_thinking:
                                yield thinking
                            pending_thinking.clear()
                            saw_tool_use = True
                            emitted_committed_output = True
                            function = bracket_tool["function"]
                            yield ToolUseStart(
                                next_index,
                                bracket_tool["id"],
                                function["name"],
                                function["arguments"],
                            )
                            next_index += 1

                    elif event_type == "toolUse":
                        saw_tool_use = True
                        tool_id = data["toolUseId"]
                        tool_input = data.get("input", "")
                        active_tool_id = tool_id

                        if tool_id not in tool_indices:
                            for thinking in pending_thinking:
                                yield thinking
                            pending_thinking.clear()
                            tool_indices[tool_id] = next_index
                            emitted_committed_output = True
                            yield ToolUseStart(
                                next_index, tool_id, data["name"], tool_input
                            )
                            next_index += 1
                        elif tool_input:
                            emitted_committed_output = True
                            yield ToolUseDelta(tool_indices[tool_id], tool_input)

                    elif event_type == "toolUseInput" and active_tool_id in tool_indices:
                        emitted_committed_output = True
                        yield ToolUseDelta(tool_indices[active_tool_id], data["input"])

                    elif event_type == "usage":
                        for thinking in pending_thinking:
                            yield thinking
                        pending_thinking.clear()
                        emitted_committed_output = True
                        yield UsageReport(
                            data.get("inputTokens") or 0,
                            data.get("outputTokens") or 0,
                        )

                    elif event_type == "error":
                        emitted_committed_output = True
                        message = (
                            data.get("message")
                            or data.get("error")
                            or "Unknown Kiro stream error"
                        )
                        yield StreamError(str(message))
                for thinking in pending_thinking:
                    yield thinking
                break
            except UpstreamError as exc:
                if (
                    exc.retryable
                    and not emitted_committed_output
                    and attempt < MAX_TRANSIENT_ATTEMPTS
                ):
                    attempt += 1
                    log.warning(
                        "Retrying transient Kiro stream exception "
                        "request_id=%s model=%s attempt=%d/%d",
                        exc.request_id or "unknown",
                        _model_id_of(state),
                        attempt,
                        MAX_TRANSIENT_ATTEMPTS,
                    )
                    await self._sleep(TRANSIENT_RETRY_DELAY_SECONDS)
                    continue

                yield StreamError(str(exc))
                finish_reason = "tool_calls" if saw_tool_use else "stop"
                yield Completed(finish_reason)
                return

        finish_reason = "tool_calls" if saw_tool_use else "stop"
        yield Completed(finish_reason)
        if thinking_events:
            log.info(
                "Kiro turn thinking model=%s thinking_events=%d thinking_chars=%d",
                _model_id_of(state),
                thinking_events,
                thinking_chars,
            )
