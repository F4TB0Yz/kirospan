"""Presenta `AssistantEvent` como Responses API de OpenAI (`/v1/responses`).

Dos modos:

- `responses_non_stream`: consume el flujo completo y devuelve el objeto
  `response` único que espera el endpoint no-streaming.
- `responses_stream`: emite los eventos SSE que espera Codex y el SDK de
  OpenAI (`response.created`, `response.in_progress`, `response.output_item.added`,
  `response.output_text.delta`/`response.function_call_arguments.delta`, ... y
  `response.completed`).

El Responses API agrupa la salida en *items* (`message` con partes
`output_text`, o `function_call`), así que el presenter mantiene el item
abierto y lo cierra con `response.output_item.done` cuando llega el fin de
turno.
"""

import json
import time
import uuid
from typing import Any

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


def _sse(event_type: str, payload: dict) -> str:
    data = {"type": event_type, **payload}
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


def _zero_usage() -> dict:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    }


def _message_item(text: str, status: str = "completed") -> dict:
    return {
        "id": f"msg_{uuid.uuid4().hex[:16]}",
        "type": "message",
        "status": status,
        "role": "assistant",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def _function_item(call: dict, status: str = "completed") -> dict:
    item = {
        "id": call["id"],
        "type": "function_call",
        "call_id": call["call_id"],
        "name": call["name"],
        "arguments": call["arguments"],
    }
    if status != "completed":
        item["status"] = status
    return item


class _StreamState:
    """Estado del stream: items abiertos, texto y uso acumulado."""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.usage = _zero_usage()
        self.final_output: list[dict] = []
        self.text_parts: list[str] = []
        self.text_item_id = f"msg_{uuid.uuid4().hex[:16]}"
        self.text_opened = False
        self.tool_items: dict[int, dict] = {}
        self.status = "in_progress"
        self.stream_error: dict | None = None

    def text_index(self) -> int:
        return len(self.final_output) - 1

    def tool_index(self, call: dict) -> int:
        for index, item in enumerate(self.final_output):
            if item.get("id") == call["id"]:
                return index
        return max(len(self.final_output) - 1, 0)


def _open_text_item(state: _StreamState) -> list[str]:
    if state.text_opened:
        return []
    state.text_opened = True
    item = _message_item("", status="in_progress")
    state.final_output.append(item)
    index = state.text_index()
    return [
        _sse("response.output_item.added", {"output_index": index, "item": item}),
        _sse(
            "response.content_part.added",
            {
                "item_id": state.text_item_id,
                "output_index": index,
                "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
        ),
    ]


def _close_text_item(state: _StreamState) -> list[str]:
    if not state.text_opened:
        return []
    state.text_opened = False
    text = "".join(state.text_parts)
    index = state.text_index()
    state.final_output[index] = _message_item(text)
    return [
        _sse(
            "response.output_text.done",
            {
                "item_id": state.text_item_id,
                "output_index": index,
                "content_index": 0,
                "text": text,
            },
        ),
        _sse(
            "response.content_part.done",
            {
                "item_id": state.text_item_id,
                "output_index": index,
                "content_index": 0,
                "part": {"type": "output_text", "text": text, "annotations": []},
            },
        ),
    ]


def _open_tool_item(state: _StreamState, call: dict) -> list[str]:
    item = _function_item(call, status="in_progress")
    state.final_output.append(item)
    index = state.text_index()
    return [
        _sse("response.output_item.added", {"output_index": index, "item": item}),
        _sse(
            "response.function_call_arguments.delta",
            {
                "item_id": item["id"],
                "output_index": index,
                "delta": call["arguments"],
            },
        ),
    ]


async def responses_non_stream(events, model_id: str) -> dict:
    """Consume el flujo y devuelve el objeto `response` completo."""
    text_parts: list[str] = []
    tool_calls: list[dict] = []
    usage = _zero_usage()
    status = "completed"
    error = None

    async for event in events:
        if isinstance(event, ThinkingDelta):
            continue

        elif isinstance(event, TextDelta):
            text_parts.append(event.text)

        elif isinstance(event, Refusal):
            text_parts.append(event.explanation)

        elif isinstance(event, ToolUseStart):
            tool_calls.append(
                {
                    "id": f"fc_{uuid.uuid4().hex[:16]}",
                    "call_id": event.call_id,
                    "name": event.name,
                    "arguments": event.arguments,
                }
            )

        elif isinstance(event, ToolUseDelta):
            if 0 <= event.index < len(tool_calls):
                tool_calls[event.index]["arguments"] += event.arguments

        elif isinstance(event, UsageReport):
            usage["input_tokens"] = event.input_tokens
            usage["output_tokens"] = event.output_tokens
            usage["total_tokens"] = event.input_tokens + event.output_tokens

        elif isinstance(event, StreamError):
            error = {"code": "upstream_error", "message": event.message, "param": None}
            status = "failed"

        elif isinstance(event, Completed):
            continue

    output: list[dict] = []
    text = "".join(text_parts)
    if text:
        output.append(_message_item(text))
    output.extend(_function_item(call) for call in tool_calls)

    return {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "status": status,
        "model": model_id,
        "output": output,
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
        "error": error,
        "reasoning": {"effort": None, "summary": None},
        "usage": usage,
    }


async def responses_stream(events, model_id: str):
    """Traduce un flujo de `AssistantEvent` a eventos SSE del Responses API."""
    response: dict[str, Any] = {
        "id": f"resp_{uuid.uuid4().hex[:24]}",
        "object": "response",
        "created_at": int(time.time()),
        "status": "in_progress",
        "model": model_id,
        "output": [],
        "parallel_tool_calls": True,
        "tools": [],
        "tool_choice": "auto",
        "error": None,
        "reasoning": {"effort": None, "summary": None},
        "usage": None,
    }
    state = _StreamState(response)

    yield _sse("response.created", {"response": response})
    yield _sse("response.in_progress", {"response": response})

    async for event in events:
        if isinstance(event, ThinkingDelta):
            continue

        elif isinstance(event, (TextDelta, Refusal)):
            text = event.text if isinstance(event, TextDelta) else event.explanation
            for line in _open_text_item(state):
                yield line
            state.text_parts.append(text)
            yield _sse(
                "response.output_text.delta",
                {
                    "item_id": state.text_item_id,
                    "output_index": state.text_index(),
                    "content_index": 0,
                    "delta": text,
                },
            )

        elif isinstance(event, ToolUseStart):
            call = {
                "id": f"fc_{uuid.uuid4().hex[:16]}",
                "call_id": event.call_id,
                "name": event.name,
                "arguments": event.arguments,
            }
            state.tool_items[event.index] = call
            for line in _open_tool_item(state, call):
                yield line

        elif isinstance(event, ToolUseDelta):
            call = state.tool_items.get(event.index)
            if call is None:
                continue
            call["arguments"] += event.arguments
            yield _sse(
                "response.function_call_arguments.delta",
                {
                    "item_id": call["id"],
                    "output_index": state.tool_index(call),
                    "delta": event.arguments,
                },
            )

        elif isinstance(event, UsageReport):
            state.usage["input_tokens"] = event.input_tokens
            state.usage["output_tokens"] = event.output_tokens
            state.usage["total_tokens"] = event.input_tokens + event.output_tokens

        elif isinstance(event, StreamError):
            state.stream_error = {
                "code": "upstream_error",
                "message": event.message,
                "param": None,
            }
            state.status = "failed"

        elif isinstance(event, Completed):
            continue

    # Cierre de items pendientes, en orden de aparición.
    for line in _close_text_item(state):
        yield line
    for call in state.tool_items.values():
        index = state.tool_index(call)
        yield _sse(
            "response.function_call_arguments.done",
            {
                "item_id": call["id"],
                "output_index": index,
                "arguments": call["arguments"],
            },
        )
        yield _sse(
            "response.output_item.done",
            {"output_index": index, "item": _function_item(call)},
        )
    for index, item in enumerate(state.final_output):
        if item["type"] == "message":
            yield _sse(
                "response.output_item.done",
                {"output_index": index, "item": item},
            )

    response["status"] = state.status
    response["output"] = state.final_output
    response["error"] = state.stream_error
    response["usage"] = state.usage
    if state.stream_error is not None:
        yield _sse("response.failed", {"response": response})
    else:
        state.status = "completed"
        response["status"] = "completed"
    yield _sse("response.completed", {"response": response})
