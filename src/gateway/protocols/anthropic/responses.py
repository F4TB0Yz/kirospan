"""Formato interno OpenAI → respuestas Anthropic."""

import json
import uuid

CHARS_PER_TOKEN = 4
TOKENS_PER_IMAGE = 1600


def _tool_use_block(tool_call: dict) -> dict:
    function = tool_call.get("function") or {}
    raw_arguments = function.get("arguments") or "{}"
    try:
        tool_input = json.loads(raw_arguments)
    except (TypeError, json.JSONDecodeError):
        tool_input = {"raw": str(raw_arguments)}
    return {
        "type": "tool_use",
        "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
        "name": function.get("name", ""),
        "input": tool_input,
    }


def anthropic_response_from_openai(response: dict) -> dict:
    """Traduce una `chat.completion` a un objeto `message` de Anthropic."""
    choice = (response.get("choices") or [{}])[0]
    message = choice.get("message") or {}

    content = []
    if message.get("reasoning_content"):
        content.append({
            "type": "thinking",
            "thinking": message["reasoning_content"],
            "signature": message.get("reasoning_signature", ""),
        })
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    elif message.get("refusal"):
        content.append({"type": "text", "text": message["refusal"]})
    content.extend(_tool_use_block(tool_call) for tool_call in message.get("tool_calls") or [])

    usage = response.get("usage") or {}
    stop_reason = {
        "tool_calls": "tool_use",
        "content_filter": "refusal",
    }.get(choice.get("finish_reason"), "end_turn")
    return {
        "id": response.get("id", f"msg_{uuid.uuid4().hex[:24]}"),
        "type": "message",
        "role": "assistant",
        "model": response.get("model", ""),
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }


def estimate_tokens(text_characters: int, image_count: int = 0) -> int:
    """Estimación aproximada: Kiro no expone un endpoint de conteo."""
    return max(1, text_characters // CHARS_PER_TOKEN + image_count * TOKENS_PER_IMAGE)


def estimate_output_tokens(characters: int) -> int:
    """Redondea hacia arriba cuando el runtime no informa del uso."""
    return max(1, (characters + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)
