"""Auto-continuación cuando el modelo anuncia trabajo pero no lo ejecuta.

Con `tool_results` en el turno actual, algunos modelos responden con un parte de
progreso ("ahora lo corrijo") y cierran el turno sin llamar a ninguna
herramienta. Esa respuesta se mueve al historial y se pide explícitamente la
acción prometida, hasta `MAX_AUTO_CONTINUATIONS` veces.
"""

import copy
import re

AUTO_CONTINUATION_PROMPT = (
    "Your previous response announced pending actions but stopped before executing them. "
    "Continue the task now. Do not repeat a progress update. "
    "Call the required tool or subagent in this response. "
    "Only provide a final answer if the user's task is actually complete or user input is required."
)
MAX_AUTO_CONTINUATIONS = 2

_FOLLOWUP_PROMISE_RE = re.compile(
    r"\b(?:corregiré|revisaré|ejecutaré|validaré|haré|aplicaré|continuaré|actualizaré|"
    r"implementaré|pediré|repetiré|ajustaré|probaré|verificaré|cerraré)\b|"
    r"\b(?:ahora|a continuación|después|luego)\b.{0,120}\b(?:corregir|revis|ejecut|"
    r"valid|hacer|aplic|continu|actualiz|implement|probar|verific|cerrar)|"
    r"\b(?:i will|i'll|next i(?:'ll| will)|now i(?:'ll| will))\b",
    re.IGNORECASE | re.DOTALL,
)


def response_promises_followup(text: str) -> bool:
    """Solo cierto ante una promesa explícita en primera persona."""
    return bool(text and _FOLLOWUP_PROMISE_RE.search(" ".join(text.split())))


def state_has_tool_results(state: dict) -> bool:
    current = (state.get("conversationState") or {}).get("currentMessage") or {}
    context = (current.get("userInputMessage") or {}).get("userInputMessageContext") or {}
    return bool(context.get("toolResults"))


def build_continuation_state(state: dict, assistant_text: str) -> dict:
    """Mueve la respuesta prematura al historial y reclama la acción prometida."""
    continued = copy.deepcopy(state)
    conversation = continued["conversationState"]

    previous_current = copy.deepcopy(conversation["currentMessage"])
    previous_user = previous_current.get("userInputMessage") or {}
    previous_user.pop("images", None)

    previous_context = previous_user.get("userInputMessageContext") or {}
    tools = previous_context.pop("tools", None)
    if previous_context:
        previous_user["userInputMessageContext"] = previous_context
    else:
        previous_user.pop("userInputMessageContext", None)

    history = conversation.setdefault("history", [])
    history.append(previous_current)
    history.append({"assistantResponseMessage": {"content": assistant_text}})

    current_user = {
        "content": AUTO_CONTINUATION_PROMPT,
        "modelId": previous_user.get("modelId", "auto"),
        "origin": previous_user.get("origin", "KIRO_CLI"),
    }
    if tools:
        current_user["userInputMessageContext"] = {"tools": tools}
    conversation["currentMessage"] = {"userInputMessage": current_user}
    return continued
