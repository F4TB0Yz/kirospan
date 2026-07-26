"""Regresiones de auto-continuación del stream Anthropic."""

import asyncio
import json

from gateway.protocols.anthropic.continuation import AUTO_CONTINUATION_PROMPT
from gateway.protocols.anthropic.sse_presenter import (
    _run_one_call,
    anthropic_stream_presenter,
)


def _openai_line(text: str = "", finish_reason=None) -> str:
    payload = {
        "choices": [{
            "delta": {"content": text} if text else {},
            "finish_reason": finish_reason,
        }]
    }
    return f"data: {json.dumps(payload)}\n\n"


async def test_text_frames_stream_before_upstream_finishes():
    release_upstream = asyncio.Event()

    async def stream_runtime(_client, _access_token, _state, _model_id):
        yield _openai_line("Hola")
        await release_upstream.wait()
        yield _openai_line(" mundo")
        yield _openai_line(finish_reason="stop")

    generator = _run_one_call(
        stream_runtime, None, "token", {}, "claude-opus-4.8", 0
    )
    first_kind, first_frame = await asyncio.wait_for(anext(generator), timeout=0.1)
    release_upstream.set()
    await generator.aclose()

    assert first_kind == "frame"
    assert '"content_block":{"type":"text"' in first_frame


async def test_promised_action_without_tool_result_does_not_auto_continue():
    states = []

    async def stream_runtime(_client, _access_token, state, _model_id):
        states.append(state)
        yield _openai_line("Buscaré el archivo exacto.")
        yield _openai_line(finish_reason="stop")

    initial_state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "¿Dónde está la conversación?",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }

    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", initial_state, "claude-opus-4.8"
        )
    ])

    assert len(states) == 1


async def test_text_block_closes_before_later_thinking_block_starts():
    async def stream_runtime(_client, _access_token, _state, _model_id):
        yield _openai_line("Respuesta parcial.")
        payload = {
            "choices": [{
                "delta": {"reasoning_content": "Reconsidero."},
                "finish_reason": None,
            }]
        }
        yield f"data: {json.dumps(payload)}\n\n"
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Responde",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }

    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    text_stop = output.index('"type":"content_block_stop","index":0')
    thinking_start = output.index('"index":1,"content_block":{"type":"thinking"')
    assert text_stop < thinking_start


async def test_text_block_closes_before_tool_block_starts():
    async def stream_runtime(_client, _access_token, _state, _model_id):
        yield _openai_line("Antes de usar herramienta.")
        payload = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "toolu_1",
                        "function": {"name": "terminal", "arguments": "{}"},
                    }]
                },
                "finish_reason": "tool_calls",
            }]
        }
        yield f"data: {json.dumps(payload)}\n\n"

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Ejecuta",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }
    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    text_stop = output.index('"type":"content_block_stop","index":0')
    tool_start = output.index('"index":1,"content_block":{"type":"tool_use"')
    assert text_stop < tool_start


async def test_tool_block_closes_before_text_block_starts():
    async def stream_runtime(_client, _access_token, _state, _model_id):
        payload = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "toolu_1",
                        "function": {"name": "terminal", "arguments": "{}"},
                    }]
                },
                "finish_reason": None,
            }]
        }
        yield f"data: {json.dumps(payload)}\n\n"
        yield _openai_line("Después de herramienta.")
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Ejecuta",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }
    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    tool_stop = output.index('"type":"content_block_stop","index":0')
    text_start = output.index('"index":1,"content_block":{"type":"text"')
    assert tool_stop < text_start


async def test_fragmented_tool_arguments_stay_in_one_block():
    async def stream_runtime(_client, _access_token, _state, _model_id):
        for tool_call in (
            {
                "index": 0,
                "id": "toolu_1",
                "function": {"name": "terminal", "arguments": '{"command"'},
            },
            {"index": 0, "function": {"arguments": ':"pwd"}'}},
        ):
            payload = {
                "choices": [{
                    "delta": {"tool_calls": [tool_call]},
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(payload)}\n\n"
        yield _openai_line(finish_reason="tool_calls")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Ejecuta",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }
    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    assert output.count('"content_block":{"type":"tool_use"') == 1
    assert output.count('"type":"input_json_delta"') == 1
    assert '\"partial_json\":\"{\\\"command\\\":\\\"pwd\\\"}\"' in output


async def test_tool_fragments_survive_interleaved_text():
    async def stream_runtime(_client, _access_token, _state, _model_id):
        chunks = (
            {
                "tool_calls": [{
                    "index": 0,
                    "id": "toolu_1",
                    "function": {"name": "terminal", "arguments": '{"command"'},
                }]
            },
            {"content": "Texto intermedio."},
            {
                "tool_calls": [{
                    "index": 0,
                    "function": {"arguments": ':"pwd"}'},
                }]
            },
        )
        for delta in chunks:
            payload = {"choices": [{"delta": delta, "finish_reason": None}]}
            yield f"data: {json.dumps(payload)}\n\n"
        yield _openai_line(finish_reason="tool_calls")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Ejecuta",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }
    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    assert output.count('"content_block":{"type":"tool_use"') == 1
    assert '"id":"toolu_1"' in output
    assert '"name":"terminal"' in output
    assert '\"partial_json\":\"{\\\"command\\\":\\\"pwd\\\"}\"' in output


async def test_thinking_signature_is_emitted_once_before_block_stop():
    async def stream_runtime(_client, _access_token, _state, _model_id):
        for text in ("Paso 1.", "Paso 2."):
            payload = {
                "choices": [{
                    "delta": {
                        "reasoning_content": text,
                        "reasoning_signature": "firma",
                    },
                    "finish_reason": None,
                }]
            }
            yield f"data: {json.dumps(payload)}\n\n"
        yield _openai_line("Respuesta.")
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Responde",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }
    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    signature = '"delta":{"type":"signature_delta","signature":"firma"}'
    thinking_stop = output.index('"type":"content_block_stop","index":0')
    assert output.count(signature) == 1
    assert output.index(signature) < thinking_stop


async def test_auto_continuation_closes_previous_text_block_first():
    calls = 0

    async def stream_runtime(_client, _access_token, _state, _model_id):
        nonlocal calls
        calls += 1
        yield _openai_line(
            "Pruebo el marcador. Mediré ambos canales."
            if calls == 1
            else "Validación terminada."
        )
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Continúa",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                    "userInputMessageContext": {
                        "toolResults": [{
                            "toolUseId": "toolu_wait",
                            "content": [{"text": "Script running with cell ID 7"}],
                            "status": "success",
                        }]
                    },
                }
            }
        }
    }
    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    first_stop = output.index('"type":"content_block_stop","index":0')
    second_start = output.index('"index":1,"content_block":{"type":"text"')
    assert calls == 2
    assert first_stop < second_start


async def test_empty_response_after_tool_result_auto_continues_same_turn():
    states = []

    async def stream_runtime(_client, _access_token, state, _model_id):
        states.append(state)
        if len(states) == 2:
            yield _openai_line("Proceso terminado y resultado validado.")
        yield _openai_line(finish_reason="stop")

    tool_results = [{
        "toolUseId": "toolu_wait",
        "content": [{"text": "Script running with cell ID 7"}],
        "status": "success",
    }]
    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                    "userInputMessageContext": {"toolResults": tool_results},
                }
            }
        }
    }

    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    assert len(states) == 2
    continued_user = states[1]["conversationState"]["currentMessage"]["userInputMessage"]
    assert continued_user["content"] == AUTO_CONTINUATION_PROMPT
    assert continued_user["userInputMessageContext"]["toolResults"] == tool_results
    assert "Proceso terminado y resultado validado." in output


async def test_empty_response_preserves_user_text_with_tool_results():
    states = []

    async def stream_runtime(_client, _access_token, state, _model_id):
        states.append(state)
        if len(states) == 2:
            yield _openai_line("Restricción conservada.")
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "NO modifiques config.py",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                    "userInputMessageContext": {
                        "toolResults": [{
                            "toolUseId": "toolu_wait",
                            "content": [{"text": "exit 1"}],
                            "status": "success",
                        }]
                    },
                }
            }
        }
    }

    _ = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    continued_content = states[1]["conversationState"]["currentMessage"][
        "userInputMessage"
    ]["content"]
    assert "NO modifiques config.py" in continued_content
    assert AUTO_CONTINUATION_PROMPT in continued_content


async def test_empty_initial_response_without_tool_result_does_not_loop():
    calls = 0

    async def stream_runtime(_client, _access_token, _state, _model_id):
        nonlocal calls
        calls += 1
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "Responde si terminaste.",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                }
            }
        }
    }

    _ = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    assert calls == 1


async def test_pending_process_message_after_tool_result_auto_continues():
    calls = 0

    async def stream_runtime(_client, _access_token, _state, _model_id):
        nonlocal calls
        calls += 1
        yield _openai_line(
            "El proceso sigue en ejecución."
            if calls == 1
            else "Proceso terminado."
        )
        yield _openai_line(finish_reason="stop")

    state = {
        "conversationState": {
            "currentMessage": {
                "userInputMessage": {
                    "content": "",
                    "modelId": "claude-opus-4.8",
                    "origin": "KIRO_CLI",
                    "userInputMessageContext": {
                        "toolResults": [{
                            "toolUseId": "toolu_wait",
                            "content": [{"text": "Script running with cell ID 7"}],
                            "status": "success",
                        }]
                    },
                }
            }
        }
    }

    output = "".join([
        frame
        async for frame in anthropic_stream_presenter(
            stream_runtime, None, "token", state, "claude-opus-4.8"
        )
    ])

    assert calls == 2
    assert "Proceso terminado." in output
