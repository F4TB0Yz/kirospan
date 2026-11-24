"""Conversión del `input`/`instructions`/`tools` del Responses API a mensajes OpenAI.

El Responses API acepta `input` como string o como lista de items
(mensajes, `function_call`, `function_call_output`, `reasoning`, etc.) y
declara las herramientas con `name`/`description`/`parameters` a nivel de
raíz. Este módulo lo normaliza al formato de mensajes de chat completions
que espera `build_conversation_state`, y las herramientas al formato
`{"type": "function", "function": {...}}`.
"""

import json
from typing import Any

TEXT_PART_TYPES = {"text", "input_text", "output_text"}


def _part_text(part: dict) -> str:
    return str(part.get("text", ""))


def content_to_text(content: Any) -> str:
    """Aplana el contenido de un item a texto (string o lista de partes)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            _part_text(part)
            for part in content
            if isinstance(part, dict) and part.get("type") in TEXT_PART_TYPES
        )
    return "" if content is None else str(content)


def _arguments_to_string(arguments: Any) -> str:
    if isinstance(arguments, dict):
        return json.dumps(arguments)
    return "" if arguments is None else str(arguments)


def _tool_calls_from_parts(parts: Any) -> list[dict]:
    """Extrae `function_call` de los `content` de un item de mensaje."""

    calls: list[dict] = []
    if not isinstance(parts, list):
        return calls
    for part in parts:
        if not isinstance(part, dict) or part.get("type") != "function_call":
            continue
        calls.append(
            {
                "id": part.get("call_id") or part.get("id") or "",
                "type": "function",
                "function": {
                    "name": part.get("name", ""),
                    "arguments": _arguments_to_string(part.get("arguments", "")),
                },
            }
        )
    return calls


def responses_input_to_messages(
    input_value: Any,
    instructions: str | None = None,
) -> list[dict]:
    """Traduce el `input` del Responses API a mensajes OpenAI de chat.

    `instructions` se antepone como mensaje de sistema. Los items que Kiro no
    puede representar (`reasoning`, `web_search`...) se descartan en silencio.
    """
    messages: list[dict] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})

    if isinstance(input_value, str):
        if input_value:
            messages.append({"role": "user", "content": input_value})
        return messages

    if not isinstance(input_value, list):
        raise ValueError("input must be a string or a list of items")

    for item in input_value:
        if not isinstance(item, dict):
            continue
        item_type = item.get("type", "message")
        role = item.get("role", "user")

        if item_type == "function_call":
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": item.get("call_id") or item.get("id") or "",
                            "type": "function",
                            "function": {
                                "name": item.get("name", ""),
                                "arguments": _arguments_to_string(
                                    item.get("arguments", "")
                                ),
                            },
                        }
                    ],
                }
            )
            continue

        if item_type == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": item.get("call_id", ""),
                    "content": "" if item.get("output") is None else str(item["output"]),
                }
            )
            continue

        if item_type == "reasoning":
            continue

        if role in {"system", "developer"}:
            messages.append(
                {"role": "system", "content": content_to_text(item.get("content"))}
            )
            continue

        if role == "assistant":
            tool_calls = _tool_calls_from_parts(item.get("content"))
            message: dict = {"role": "assistant"}
            text = content_to_text(item.get("content"))
            message["content"] = text or None
            if tool_calls:
                message["tool_calls"] = tool_calls
            messages.append(message)
            continue

        if role != "user":
            continue

        messages.append(
            {"role": "user", "content": content_to_text(item.get("content"))}
        )

    return messages


def responses_tools_to_chat(tools: list[dict] | None) -> list[dict] | None:
    """Traduce herramientas del Responses API al formato de chat completions.

    Las herramientas host (`web_search_preview`, `computer_use`...) no tienen
    equivalente en Kiro y se descartan.
    """
    if not tools:
        return None
    chat_tools: list[dict] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        chat_tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                },
            }
        )
    return chat_tools or None


def responses_reasoning_effort(reasoning: Any) -> str | None:
    """Extrae `reasoning.effort` (o el string directo) del parámetro `reasoning`."""
    if reasoning is None:
        return None
    if isinstance(reasoning, str):
        return reasoning
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
        return effort if isinstance(effort, str) else None
    return None