"""Construcción del estado de conversación que espera el runtime de Kiro.

Kiro no acepta una lista plana de mensajes: exige un historial de turnos que
alternen estrictamente `userInputMessage` y `assistantResponseMessage`, con el
último turno separado en `currentMessage`. Las reglas que impone —y que este
módulo implementa— no están documentadas y se derivaron por prueba y error
contra el runtime:

- El prompt de sistema no es un rol: se antepone al contenido del primer
  mensaje de usuario.
- Dos mensajes de usuario seguidos rompen la alternancia, así que se fusionan.
- El rol `tool` tampoco existe: sus resultados van en `userInputMessageContext`
  del turno de usuario anterior, cuyo texto se marca con una nota.
- Las herramientas usadas en turnos pasados deben volver a declararse, o el
  runtime rechaza sus resultados con HTTP 400.
"""

import json
import uuid

from ...domain.images import extract_kiro_images, validate_kiro_image_count
from .tools import add_placeholder_tools, convert_tools_to_kiro

ORIGIN = "KIRO_CLI"
CHAT_TRIGGER_TYPE = "MANUAL"
AGENT_TASK_TYPE = "vibe"

DEFAULT_GREETING = "Hello"
TOOL_RESULTS_NOTE = "Tool results provided."
PROCEED_PROMPT = "Please proceed with the task."
IMAGE_ONLY_CONTENT = "Image provided."

TOOL_CONTINUATION_PROMPT = (
    "Continue executing the user's task now. "
    "Do not end the turn after describing a next step. "
    "If another tool or subagent is needed, call it in this response. "
    "Only stop when the requested task is complete or user input is required."
)

_TEXT_BLOCK_TYPES = {"text", "output_text"}


def content_text(message: dict) -> str:
    """Aplana el contenido de un mensaje a texto.

    Los bloques se concatenan sin separador, a diferencia del aplanado del
    protocolo Anthropic, que une con saltos de línea.
    """
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    return "".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") in _TEXT_BLOCK_TYPES
    )


def _tool_use_input(function: dict) -> dict:
    arguments = function.get("arguments", "{}")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}
    return arguments if isinstance(arguments, dict) else {}


def assistant_response(message: dict) -> dict:
    """Traduce un mensaje del asistente a `assistantResponseMessage`."""
    response = {"content": content_text(message)}
    tool_uses = [
        {
            "name": (function := tool_call.get("function") or {}).get("name", ""),
            "toolUseId": tool_call.get("id", ""),
            "input": _tool_use_input(function),
        }
        for tool_call in message.get("tool_calls") or []
    ]
    if tool_uses:
        response["toolUses"] = tool_uses
    return response


def tool_result(message: dict) -> dict:
    """Traduce un mensaje con rol `tool` a un `toolResult` de Kiro."""
    return {
        "content": [{"text": content_text(message)}],
        "status": "error" if message.get("is_error") else "success",
        "toolUseId": message.get("tool_call_id", ""),
    }


def _split_system_prompt(messages: list[dict], system_prompt: str) -> tuple[str, list[dict]]:
    """Separa los mensajes de sistema del resto y los concatena."""
    system_parts = [system_prompt] if system_prompt else []
    conversation = []
    for message in messages:
        if message.get("role") == "system":
            content = message.get("content", "")
            if content:
                system_parts.append(str(content))
        else:
            conversation.append(message)
    return "\n\n".join(system_parts), conversation


def _current_turn_start(messages: list[dict]) -> int:
    """Índice donde empieza el turno actual.

    Retrocede sobre los mensajes `tool` finales para que sus resultados viajen
    en `currentMessage`, y no en el historial.
    """
    start = len(messages) - 1
    while start > 0 and messages[start].get("role") == "tool":
        start -= 1
    if messages[start].get("role") == "assistant" and not (messages[start].get("tool_calls") or []):
        start += 1
    return start


def _user_input_message(content: str, model_id: str) -> dict:
    return {"content": content, "modelId": model_id, "origin": ORIGIN}


def _last_user_message(history: list[dict]) -> dict | None:
    return history[-1].get("userInputMessage") if history else None


def _append_user(history: list[dict], content: str, model_id: str) -> None:
    """Añade un turno de usuario, fusionándolo con el anterior si lo hubiera."""
    previous = _last_user_message(history)
    if previous is not None:
        previous["content"] += f"\n\n{content}"
    else:
        history.append({"userInputMessage": _user_input_message(content, model_id)})


def _append_tool_results(history: list[dict], results: list[dict], model_id: str) -> None:
    """Cuelga los resultados del turno de usuario anterior, o crea uno."""
    previous = _last_user_message(history)
    if previous is not None:
        previous["content"] += f"\n\n{TOOL_RESULTS_NOTE}"
        context = previous.setdefault("userInputMessageContext", {})
        context.setdefault("toolResults", []).extend(results)
        return
    message = _user_input_message(TOOL_RESULTS_NOTE, model_id)
    message["userInputMessageContext"] = {"toolResults": results}
    history.append({"userInputMessage": message})


def _build_history(
    messages: list[dict],
    model_id: str,
    effective_system: str,
) -> tuple[list[dict], bool]:
    """Construye el historial alternante. Devuelve también si insertó el system."""
    history: list[dict] = []
    system_prepended = False
    index = 0

    while index < len(messages):
        message = messages[index]
        role = message.get("role")

        if role == "user":
            content = content_text(message)
            if effective_system and not system_prepended:
                content = f"{effective_system}\n\n{content}"
                system_prepended = True
            _append_user(history, content, model_id)

        elif role == "assistant":
            response = assistant_response(message)
            if response.get("content") or response.get("toolUses"):
                history.append({"assistantResponseMessage": response})

        elif role == "tool":
            results = [tool_result(message)]
            next_index = index + 1
            while next_index < len(messages) and messages[next_index].get("role") == "tool":
                results.append(tool_result(messages[next_index]))
                next_index += 1
            index = next_index - 1
            _append_tool_results(history, results, model_id)

        index += 1

    return history, system_prepended


def _build_current_turn(
    messages: list[dict],
    history: list[dict],
    effective_system: str,
    system_prepended: bool,
) -> tuple[str, list[dict]]:
    """Resuelve el contenido y los `toolResults` del turno actual."""
    first = messages[0] if messages else None
    if first is None:
        return PROCEED_PROMPT, []

    role = first.get("role")

    if role == "assistant":
        response = assistant_response(first)
        if response.get("content") or response.get("toolUses"):
            history.append({"assistantResponseMessage": response})
        results = [
            tool_result(message) for message in messages[1:] if message.get("role") == "tool"
        ]
        return (TOOL_RESULTS_NOTE if results else PROCEED_PROMPT), results

    if role == "tool":
        results = [tool_result(message) for message in messages if message.get("role") == "tool"]
        return TOOL_RESULTS_NOTE, results

    if role == "user":
        content = content_text(first)
        results = [
            tool_result(message) for message in messages[1:] if message.get("role") == "tool"
        ]
        if effective_system and not system_prepended:
            content = f"{effective_system}\n\n{content}"
        return content, results

    return PROCEED_PROMPT, []


def build_conversation_state(
    model_id: str,
    messages: list[dict],
    system_prompt: str = "",
    tools: list[dict] | None = None,
    enforce_tool_continuation: bool = False,
    conversation_id: str | None = None,
) -> dict:
    """Traduce mensajes en formato OpenAI al protocolo de conversación de Kiro."""
    effective_system, conversation = _split_system_prompt(messages, system_prompt)
    kiro_tools = convert_tools_to_kiro(tools or [])

    if not conversation:
        conversation = [{"role": "user", "content": DEFAULT_GREETING}]

    current_start = _current_turn_start(conversation)
    history, system_prepended = _build_history(
        conversation[:current_start], model_id, effective_system
    )
    current_messages = conversation[current_start:]
    current_content, current_tool_results = _build_current_turn(
        current_messages, history, effective_system, system_prepended
    )

    current_images = validate_kiro_image_count([
        image
        for message in current_messages
        if message.get("role") in {"user", "tool"}
        for image in extract_kiro_images(message)
    ])
    if current_images and not current_content.strip():
        current_content = IMAGE_ONLY_CONTENT

    if enforce_tool_continuation and current_tool_results:
        current_content = f"{current_content}\n\n{TOOL_CONTINUATION_PROMPT}"

    final_tools = add_placeholder_tools(kiro_tools, history) if history else kiro_tools
    context = {}
    if current_tool_results:
        context["toolResults"] = current_tool_results
    if final_tools:
        context["tools"] = final_tools

    current_message = _user_input_message(current_content, model_id)
    if current_images:
        current_message["images"] = current_images
    if context:
        current_message["userInputMessageContext"] = context

    state = {
        "chatTriggerType": CHAT_TRIGGER_TYPE,
        "agentTaskType": AGENT_TASK_TYPE,
        "conversationId": conversation_id or str(uuid.uuid4()),
        "currentMessage": {"userInputMessage": current_message},
    }
    if history:
        state["history"] = history
    return {"conversationState": state}
