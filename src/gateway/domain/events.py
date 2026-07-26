"""Eventos de dominio que emite una respuesta del asistente.

Sustituyen al doble trabajo de decodificar el EventStream de Kiro y volver a
serializarlo a JSON entre capas: `_anthropic_stream_runtime` reparseaba con
`json.loads` las líneas `data: {` que `_stream_runtime` acababa de construir.
Con un único productor de estos eventos, cada presenter (OpenAI SSE, Anthropic
SSE, respuesta única) los consume directamente, sin ida y vuelta por JSON.

Se usan tuplas con nombre y no dataclasses para que sean inmutables por
construcción y comparables por valor sin código adicional.
"""

from typing import Literal, NamedTuple

FinishReason = Literal["stop", "tool_calls"]


class ThinkingDelta(NamedTuple):
    """Fragmento de reasoning/thinking del asistente."""

    text: str
    signature: str = ""


class TextDelta(NamedTuple):
    """Fragmento de texto del asistente."""

    text: str


class ToolUseStart(NamedTuple):
    """Comienzo de una tool call, con el primer fragmento de argumentos.

    `index` es la posición de la tool call dentro de la respuesta, en el orden
    en que Kiro las emite: los presenters lo usan para abrir el bloque
    correspondiente en su propio formato.
    """

    index: int
    call_id: str
    name: str
    arguments: str


class ToolUseDelta(NamedTuple):
    """Fragmento adicional de argumentos de una tool call ya abierta."""

    index: int
    arguments: str


class UsageReport(NamedTuple):
    """Consumo de tokens informado por el runtime."""

    input_tokens: int
    output_tokens: int


class StreamError(NamedTuple):
    """Error de nivel de evento reportado por el runtime, no HTTP."""

    message: str


class Completed(NamedTuple):
    """Fin de la respuesta."""

    finish_reason: FinishReason


AssistantEvent = (
    ThinkingDelta | TextDelta | ToolUseStart | ToolUseDelta | UsageReport | StreamError | Completed
)
