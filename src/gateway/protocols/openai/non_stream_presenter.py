"""Agrega un flujo de `AssistantEvent` en una única `chat.completion`."""

import time
import uuid

from ...domain.events import (
    Completed,
    Refusal,
    StreamError,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageReport,
)


async def openai_non_stream(events, model_id: str) -> dict:
    """Consume el flujo completo y devuelve el objeto `chat.completion`.

    `ChatService` ya limpia el texto de las tool calls en formato bracket
    evento a evento, así que aquí no hace falta volver a tocarlo.
    """
    full_content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_signature = ""
    tool_calls: list[dict] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    finish_reason = "stop"
    refusal = None

    async for event in events:
        if isinstance(event, ThinkingDelta):
            reasoning_parts.append(event.text)
            if event.signature:
                reasoning_signature = event.signature

        elif isinstance(event, TextDelta):
            full_content_parts.append(event.text)

        elif isinstance(event, ToolUseStart):
            tool_calls.append({
                "id": event.call_id,
                "type": "function",
                "function": {"name": event.name, "arguments": event.arguments},
            })

        elif isinstance(event, ToolUseDelta):
            tool_calls[event.index]["function"]["arguments"] += event.arguments

        elif isinstance(event, UsageReport):
            usage["prompt_tokens"] = event.input_tokens
            usage["completion_tokens"] = event.output_tokens
            usage["total_tokens"] = event.input_tokens + event.output_tokens

        elif isinstance(event, StreamError):
            # El monolito solo registraba el error a nivel de evento y seguía
            # acumulando contenido; se conserva ese comportamiento.
            pass

        elif isinstance(event, Refusal):
            refusal = event.explanation

        elif isinstance(event, Completed):
            finish_reason = event.finish_reason

    content = "".join(full_content_parts).strip()
    message = {"role": "assistant", "content": content if content else None}
    if refusal:
        message["refusal"] = refusal
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
        if reasoning_signature:
            message["reasoning_signature"] = reasoning_signature
    if tool_calls:
        message["tool_calls"] = tool_calls

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_id,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": usage,
    }
