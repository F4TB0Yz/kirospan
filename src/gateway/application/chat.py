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

from ..domain.errors import UpstreamError
from ..domain.events import (
    AssistantEvent,
    Completed,
    StreamError,
    TextDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageReport,
)
from ..protocols.kiro.bracket_tools import parse_bracket_tool_calls
from ..protocols.kiro.event_decoder import parse_kiro_event


class ChatService:
    """Traduce el stream de Kiro a `AssistantEvent`, una sola vez por turno."""

    def __init__(self, runtime_client) -> None:
        self._runtime_client = runtime_client

    async def run(self, *, access_token: str, state: dict):
        """Genera los eventos de una respuesta completa del asistente."""
        tool_indices: dict[str, int] = {}
        active_tool_id: str | None = None
        next_index = 0
        saw_tool_use = False

        try:
            async for raw_event in self._runtime_client.stream(
                access_token=access_token, state=state
            ):
                parsed = parse_kiro_event(raw_event)
                if not parsed:
                    continue
                event_type, data = parsed["type"], parsed["data"]

                if event_type == "content":
                    bracket_tools, cleaned_text = parse_bracket_tool_calls(data)
                    if cleaned_text:
                        yield TextDelta(cleaned_text)
                    for bracket_tool in bracket_tools:
                        saw_tool_use = True
                        function = bracket_tool["function"]
                        yield ToolUseStart(
                            next_index, bracket_tool["id"], function["name"], function["arguments"]
                        )
                        next_index += 1

                elif event_type == "toolUse":
                    saw_tool_use = True
                    tool_id = data["toolUseId"]
                    tool_input = data.get("input", "")
                    active_tool_id = tool_id

                    if tool_id not in tool_indices:
                        tool_indices[tool_id] = next_index
                        yield ToolUseStart(next_index, tool_id, data["name"], tool_input)
                        next_index += 1
                    elif tool_input:
                        yield ToolUseDelta(tool_indices[tool_id], tool_input)

                elif event_type == "toolUseInput" and active_tool_id in tool_indices:
                    yield ToolUseDelta(tool_indices[active_tool_id], data["input"])

                elif event_type == "usage":
                    yield UsageReport(data.get("inputTokens") or 0, data.get("outputTokens") or 0)

                elif event_type == "error":
                    message = data.get("message") or data.get("error") or "Unknown Kiro stream error"
                    yield StreamError(str(message))
        except UpstreamError as exc:
            yield StreamError(str(exc))
            yield Completed("stop")
            return

        finish_reason = "tool_calls" if saw_tool_use else "stop"
        yield Completed(finish_reason)
