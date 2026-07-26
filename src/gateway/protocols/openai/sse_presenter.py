"""Presenta `AssistantEvent` como chunks SSE del formato OpenAI."""

import json
import time
import uuid

from ...domain.events import Completed, StreamError, TextDelta, ThinkingDelta, ToolUseDelta, ToolUseStart, UsageReport


def _chunk(completion_id: str, created: int, model_id: str, delta: dict, finish_reason=None) -> dict:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


async def openai_stream(events, model_id: str):
    """Traduce un flujo de `AssistantEvent` a líneas `data: ...` de OpenAI."""
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    async for event in events:
        if isinstance(event, ThinkingDelta):
            delta = {"reasoning_content": event.text}
            if event.signature:
                delta["reasoning_signature"] = event.signature
            yield f"data: {json.dumps(_chunk(completion_id, created, model_id, delta))}\n\n"

        elif isinstance(event, TextDelta):
            yield f"data: {json.dumps(_chunk(completion_id, created, model_id, {'content': event.text}))}\n\n"

        elif isinstance(event, ToolUseStart):
            delta = {
                "index": event.index,
                "id": event.call_id,
                "type": "function",
                "function": {"name": event.name, "arguments": event.arguments},
            }
            yield f"data: {json.dumps(_chunk(completion_id, created, model_id, {'tool_calls': [delta]}))}\n\n"

        elif isinstance(event, ToolUseDelta):
            delta = {"index": event.index, "function": {"arguments": event.arguments}}
            yield f"data: {json.dumps(_chunk(completion_id, created, model_id, {'tool_calls': [delta]}))}\n\n"

        elif isinstance(event, UsageReport):
            usage["prompt_tokens"] = event.input_tokens
            usage["completion_tokens"] = event.output_tokens
            usage["total_tokens"] = event.input_tokens + event.output_tokens

        elif isinstance(event, StreamError):
            message = f"[Kiro error: {event.message}]"
            yield f"data: {json.dumps(_chunk(completion_id, created, model_id, {'content': message}))}\n\n"

        elif isinstance(event, Completed):
            final = _chunk(completion_id, created, model_id, {}, event.finish_reason)
            final["usage"] = usage
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"
