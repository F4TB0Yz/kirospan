"""Presenta un stream SSE de OpenAI como el stream SSE del protocolo Anthropic.

Reemplaza a `_anthropic_stream_runtime`, que reparseaba con `json.loads` cada
línea `data: {` emitida por la ruta OpenAI. La responsabilidad de esa
traducción se concentra aquí, en un solo sitio.

Lleva también el bucle de auto-continuación: si el modelo anuncia trabajo
pendiente y cierra el turno sin llamar a ninguna herramienta, se reconstruye
el estado y se vuelve a pedir la acción, hasta `MAX_AUTO_CONTINUATIONS` veces.

Recibe `stream_runtime` como colaborador (no lo importa) para que quien
compone el presenter decida qué implementación de streaming OpenAI usar —el
shim de compatibilidad necesita que sea el símbolo parcheable del módulo.
"""

import json
import logging
import uuid

from .continuation import (
    MAX_AUTO_CONTINUATIONS,
    build_continuation_state,
    response_promises_followup,
    state_has_tool_results,
)
from .responses import estimate_output_tokens
from .sse import anthropic_sse

log = logging.getLogger("kirospan")


async def _run_one_call(stream_runtime, client, access_token, state, model_id, next_block_index):
    """Consume una pasada de `stream_runtime` y emite los frames SSE de Anthropic.

    Devuelve el texto emitido, los tokens y caracteres de esta pasada, si vio
    alguna tool call, y el `finish_reason` reportado.
    """
    text_block_index = None
    tool_blocks: dict[int, int] = {}
    open_blocks: set[int] = set()
    response_text_parts: list[str] = []
    output_tokens = 0
    output_characters = 0
    saw_tool = False
    finish_reason = None

    async for line in stream_runtime(client, access_token, state, model_id):
        if not line.startswith("data: {"):
            continue
        payload = json.loads(line.removeprefix("data: ").strip())
        usage = payload.get("usage") or {}
        reported_tokens = usage.get("completion_tokens") or 0
        if reported_tokens:
            output_tokens = reported_tokens
        choice = (payload.get("choices") or [{}])[0]
        delta = choice.get("delta") or {}

        if delta.get("content") is not None:
            text = delta["content"]
            response_text_parts.append(text)
            output_characters += len(text)
            if text_block_index is None:
                text_block_index = next_block_index
                next_block_index += 1
                open_blocks.add(text_block_index)
                yield ("frame", anthropic_sse("content_block_start", {
                    "type": "content_block_start",
                    "index": text_block_index,
                    "content_block": {"type": "text", "text": ""},
                }))
            yield ("frame", anthropic_sse("content_block_delta", {
                "type": "content_block_delta",
                "index": text_block_index,
                "delta": {"type": "text_delta", "text": text},
            }))

        for tool_call in delta.get("tool_calls") or []:
            saw_tool = True
            openai_index = int(tool_call.get("index", 0))
            function = tool_call.get("function") or {}
            if openai_index not in tool_blocks:
                block_index = next_block_index
                next_block_index += 1
                tool_blocks[openai_index] = block_index
                open_blocks.add(block_index)
                yield ("frame", anthropic_sse("content_block_start", {
                    "type": "content_block_start",
                    "index": block_index,
                    "content_block": {
                        "type": "tool_use",
                        "id": tool_call.get("id") or f"toolu_{uuid.uuid4().hex[:20]}",
                        "name": function.get("name", ""),
                        "input": {},
                    },
                }))
            arguments = function.get("arguments")
            if arguments:
                output_characters += len(arguments)
                yield ("frame", anthropic_sse("content_block_delta", {
                    "type": "content_block_delta",
                    "index": tool_blocks[openai_index],
                    "delta": {"type": "input_json_delta", "partial_json": arguments},
                }))

        if choice.get("finish_reason") is not None:
            finish_reason = choice["finish_reason"]
            break

    yield (
        "result",
        {
            "next_block_index": next_block_index,
            "open_blocks": open_blocks,
            "text": "".join(response_text_parts),
            "output_tokens": output_tokens,
            "output_characters": output_characters,
            "saw_tool": saw_tool,
            "finish_reason": finish_reason or "stop",
        },
    )


async def anthropic_stream_presenter(stream_runtime, client, access_token, state, model_id):
    """Genera el stream SSE completo de `/v1/messages`, con auto-continuación."""
    message_id = f"msg_{uuid.uuid4().hex[:24]}"
    yield anthropic_sse("message_start", {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": model_id,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": 0, "output_tokens": 0},
        },
    })

    next_block_index = 0
    all_open_blocks: set[int] = set()
    total_output_tokens = 0
    total_output_characters = 0
    runtime_state = state
    continuation_attempts = 0
    continuation_allowed = state_has_tool_results(state)
    final_finish_reason = "stop"

    while True:
        call_result = None
        async for kind, payload in _run_one_call(
            stream_runtime, client, access_token, runtime_state, model_id, next_block_index
        ):
            if kind == "frame":
                yield payload
            else:
                call_result = payload

        next_block_index = call_result["next_block_index"]
        all_open_blocks |= call_result["open_blocks"]
        call_output_tokens = call_result["output_tokens"]
        call_output_characters = call_result["output_characters"]
        if call_output_tokens <= 0 and call_output_characters:
            call_output_tokens = estimate_output_tokens(call_output_characters)
        total_output_tokens += call_output_tokens
        total_output_characters += call_output_characters

        should_continue = (
            continuation_allowed
            and continuation_attempts < MAX_AUTO_CONTINUATIONS
            and call_result["finish_reason"] == "stop"
            and not call_result["saw_tool"]
            and response_promises_followup(call_result["text"])
        )
        if should_continue:
            continuation_attempts += 1
            log.warning(
                "Anthropic auto-continuation %d/%d after unfinished progress response",
                continuation_attempts,
                MAX_AUTO_CONTINUATIONS,
            )
            runtime_state = build_continuation_state(runtime_state, call_result["text"])
            continue

        final_finish_reason = call_result["finish_reason"]
        break

    if total_output_tokens <= 0 and total_output_characters:
        total_output_tokens = estimate_output_tokens(total_output_characters)
    for block_index in sorted(all_open_blocks):
        yield anthropic_sse("content_block_stop", {"type": "content_block_stop", "index": block_index})

    stop_reason = "tool_use" if final_finish_reason == "tool_calls" else "end_turn"
    yield anthropic_sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": stop_reason, "stop_sequence": None},
        "usage": {"output_tokens": total_output_tokens},
    })
    yield anthropic_sse("message_stop", {"type": "message_stop"})
