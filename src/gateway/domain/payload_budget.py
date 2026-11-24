"""Guarda de tamaño para el runtime de Kiro.

El runtime `runtime.<region>.kiro.dev/generateAssistantResponse` rechaza con
HTTP 400 y `{"reason": "CONTENT_LENGTH_EXCEEDS_THRESHOLD"}` las peticiones cuyo
contenido supere un presupuesto fijo. Ni el presupuesto ni el modo de contar
están documentados; ambos se midieron empíricamente contra el runtime vivo con
`glm-5`, y el límite es el mismo para `claude-sonnet-4.5`, `deepseek-3.2` y
`minimax-m2.5`.

Cómo cuenta Kiro (derivado de las mediciones, no de documentación):

    unidades = suma de caracteres de contenido + ~930 por mensaje del historial

Mediciones que fijan el modelo (presupuesto ≈ 1.066.700 unidades):

    1 mensaje,  1_066_650 chars -> 200
    1 mensaje,  1_066_777 chars -> 400 CONTENT_LENGTH_EXCEEDS_THRESHOLD
    401 msgs,    680_411 chars  -> 200   (680_411 + 401*930 = 1_053_741)
    401 msgs,    720_411 chars  -> 400   (720_411 + 401*930 = 1_093_341)

El detalle importante: **el límite no es sobre bytes del body JSON**. Un body de
1.0 MB con 401 mensajes lo revienta mientras un body de 1.07 MB con un solo
mensaje pasa. Contar bytes, como se hacía antes, daba falsos negativos: la
petición se truncaba hasta "caber en 1 MB" y Kiro la seguía rechazando.

De ahí `FIXED_OVERHEAD_PER_MESSAGE = 930` y `DEFAULT_MAX_PAYLOAD_UNITS =
1_060_000` (margen sobre 1.066.700 para `profileArn`, `agentMode`,
`additionalModelRequestFields` y las herramientas que el router añade después).

Los clientes interactivos (Claude Code, kiro-cli) esquivan el límite con
`/compact`; un agente de larga duración no lo hace, así que el gateway recorta
por su cuenta, con un log legible en vez de un 400 opaco.

Se descartan los turnos MÁS ANTIGUOS del historial. Nunca se recorta el turno
actual: si ni siquiera él cabe, se lanza `InvalidRequest` (un 400 legible) en
vez de mandar algo que el runtime rechazará igual.
"""

import json

from .errors import InvalidRequest

# Presupuesto en unidades medidas (contenido + overhead por mensaje).
DEFAULT_MAX_PAYLOAD_UNITS = 1_060_000

# Costo aproximado, en unidades, de cada mensaje del historial por sí mismo.
FIXED_OVERHEAD_PER_MESSAGE = 930


def _message_text_length(message: dict) -> int:
    """Caracteres de contenido de un mensaje del protocolo de Kiro.

    Un mensaje puede ser `userInputMessage` (con `content` y, opcionalmente,
    `userInputMessageContext.toolResults`) o `assistantResponseMessage` (con
    `content` y `toolUses`).
    """
    total = 0

    user_input = message.get("userInputMessage")
    if isinstance(user_input, dict):
        total += len(str(user_input.get("content") or ""))
        context = user_input.get("userInputMessageContext") or {}
        for result in context.get("toolResults") or []:
            for block in result.get("content") or []:
                total += len(str(block.get("text") or ""))

    assistant = message.get("assistantResponseMessage")
    if isinstance(assistant, dict):
        total += len(str(assistant.get("content") or ""))
        for tool_use in assistant.get("toolUses") or []:
            total += len(str(tool_use.get("name") or ""))
            total += len(json.dumps(tool_use.get("input") or {}, ensure_ascii=False))

    return total


def payload_units(state: dict) -> int:
    """Unidades que cuenta Kiro: contenido + overhead por mensaje.

    Incluye el turno actual y las herramientas declaradas, que también se
    serializan en la petición.
    """
    conversation = state.get("conversationState", {})
    messages = list(conversation.get("history") or [])
    current = (conversation.get("currentMessage") or {}).get("userInputMessage")
    if isinstance(current, dict):
        messages.append({"userInputMessage": current})

    units = sum(_message_text_length(message) + FIXED_OVERHEAD_PER_MESSAGE
                for message in messages)

    # Las herramientas viajan en el turno actual y también consumen presupuesto.
    tools = ((current or {}).get("userInputMessageContext") or {}).get("tools") or []
    units += len(json.dumps(tools, ensure_ascii=False))

    return units


def payload_size(state: dict) -> int:
    """Alias histórico: unidades medidas, no bytes del body."""
    return payload_units(state)


def _history_of(state: dict) -> list:
    return state.get("conversationState", {}).get("history", [])


def _set_history(state: dict, history: list) -> None:
    conversation = state.setdefault("conversationState", {})
    if history:
        conversation["history"] = history
    else:
        conversation.pop("history", None)


def enforce_payload_budget(
    state: dict,
    max_units: int = DEFAULT_MAX_PAYLOAD_UNITS,
    model_id: str = "unknown",
) -> int:
    """Recorta el historial hasta que `state` quepa en `max_units`.

    Devuelve cuántos turnos se descartaron (0 si no hizo falta tocar nada).
    Lanza `InvalidRequest` si el turno actual por sí solo excede el presupuesto,
    porque truncarlo cambiaría la pregunta del usuario.
    """
    units = payload_units(state)
    if units <= max_units:
        return 0

    history = _history_of(state)
    original_turns = len(history)

    # El costo decrece de forma monótona al descartar turnos del principio, así
    # que se bisecta el punto de corte mínimo que cabe en vez de recalcular por
    # cada turno descartado.
    def units_with(drop: int) -> int:
        _set_history(state, history[drop:])
        return payload_units(state)

    low, high = 0, original_turns
    while low < high:
        mid = (low + high) // 2
        if units_with(mid) <= max_units:
            high = mid
        else:
            low = mid + 1

    dropped = low
    _set_history(state, history[dropped:])
    final_units = payload_units(state)

    if final_units <= max_units:
        return dropped

    # Ni con el historial vacío cabe: el exceso está en el turno actual o en las
    # herramientas. Mandarlo daría un 400 opaco del runtime, así que se devuelve
    # un 400 legible.
    raise InvalidRequest(
        "La petición excede el límite del runtime de Kiro (~1 MB de contenido) "
        f"incluso tras descartar los {original_turns} turnos de historial "
        f"(actual: {final_units} unidades, límite: {max_units}, modelo: {model_id}). "
        "Reduce el mensaje actual, los adjuntos o las herramientas declaradas."
    )
