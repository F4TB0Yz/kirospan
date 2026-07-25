"""Anthropic Messages → formato interno OpenAI.

El gateway usa el formato OpenAI como representación intermedia, así que las
peticiones Anthropic se traducen antes de construir el estado de conversación
de Kiro. Los bloques `tool_use` pasan a `tool_calls` del asistente y los
`tool_result` a mensajes con rol `tool`.
"""

import json
import uuid


def anthropic_content_text(content) -> str:
    """Aplana bloques de contenido Anthropic a texto, unidos por saltos de línea."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and block.get("text")
    )


def _assistant_message(blocks: list[dict]) -> dict:
    tool_calls = [
        {
            "id": block.get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
            "type": "function",
            "function": {
                "name": block.get("name", ""),
                "arguments": json.dumps(block.get("input") or {}, separators=(",", ":")),
            },
        }
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "tool_use"
    ]
    message = {"role": "assistant", "content": anthropic_content_text(blocks)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _tool_result_content(block: dict):
    """El contenido de un `tool_result` puede ser texto o bloques con imágenes."""
    content = block.get("content", "")
    if not isinstance(content, list):
        return str(content)

    text = anthropic_content_text(content)
    images = [item for item in content if isinstance(item, dict) and item.get("type") == "image"]
    if not images:
        return text
    return ([{"type": "text", "text": text}] if text else []) + images


def _user_messages(blocks: list[dict]) -> list[dict]:
    text = anthropic_content_text(blocks)
    images = [
        block for block in blocks if isinstance(block, dict) and block.get("type") == "image"
    ]
    tool_results = [
        block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_result"
    ]

    messages: list[dict] = []
    if text or images:
        if images:
            content = ([{"type": "text", "text": text}] if text else []) + images
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": text})

    for block in tool_results:
        messages.append({
            "role": "tool",
            "tool_call_id": block.get("tool_use_id", ""),
            "content": _tool_result_content(block),
            "is_error": bool(block.get("is_error")),
        })

    if not text and not images and not tool_results:
        # Turno vacío: Kiro exige alternancia, así que se mantiene el hueco.
        messages.append({"role": "user", "content": ""})

    return messages


def convert_anthropic_request(request) -> tuple[list[dict], list[dict]]:
    """Devuelve `(mensajes en formato OpenAI, herramientas en formato OpenAI)`."""
    messages: list[dict] = []

    system_text = anthropic_content_text(request.system)
    if system_text:
        messages.append({"role": "system", "content": system_text})

    for message in request.messages:
        role = message.get("role")
        content = message.get("content", "")
        if isinstance(content, str):
            messages.append({"role": role, "content": content})
            continue

        blocks = content if isinstance(content, list) else []
        if role == "assistant":
            messages.append(_assistant_message(blocks))
        else:
            messages.extend(_user_messages(blocks))

    tools = [
        {
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
            },
        }
        for tool in request.tools or []
    ]
    return messages, tools
