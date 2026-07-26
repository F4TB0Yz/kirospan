"""Tests unitarios de `gateway.protocols`, sin red ni parcheo de módulos."""

import json

import pytest

from gateway.protocols.anthropic.continuation import (
    AUTO_CONTINUATION_PROMPT,
    build_continuation_state,
    response_promises_followup,
    state_has_tool_results,
)
from gateway.protocols.anthropic.requests import (
    anthropic_content_text,
    convert_anthropic_request,
)
from gateway.protocols.anthropic.responses import (
    anthropic_response_from_openai,
    estimate_output_tokens,
    estimate_tokens,
)
from gateway.protocols.anthropic.sse import anthropic_sse
from gateway.protocols.kiro.bracket_tools import parse_bracket_tool_calls
from gateway.protocols.kiro.event_decoder import parse_kiro_event
from gateway.protocols.kiro.tools import add_placeholder_tools, convert_tools_to_kiro
from gateway.protocols.openai.models import FALLBACK_MODELS, to_openai_model_list


class _Request:
    """Sustituto mínimo de `AnthropicRequest`, para no depender de pydantic."""

    def __init__(self, messages, system=None, tools=None):
        self.messages = messages
        self.system = system
        self.tools = tools


class TestEventDecoder:
    def test_readable_reasoning_event(self):
        assert parse_kiro_event(
            {"reasoningContent": {"text": "plan", "signature": "firma"}}
        ) == {"type": "thinking", "data": {"text": "plan", "signature": "firma"}}

    def test_readable_reasoning_is_inferred_without_event_type_wrapper(self):
        assert parse_kiro_event({"text": "plan", "signature": "firma"}) == {
            "type": "thinking",
            "data": {"text": "plan", "signature": "firma"},
        }

    def test_reasoning_text_and_signature_frames_are_detected_separately(self):
        assert parse_kiro_event({"text": "plan"}) == {
            "type": "thinking",
            "data": {"text": "plan", "signature": ""},
        }
        assert parse_kiro_event({"signature": "firma"}) == {
            "type": "thinking",
            "data": {"text": "", "signature": "firma"},
        }

    def test_redacted_reasoning_is_recognized_but_not_exposed(self):
        assert parse_kiro_event(
            {"reasoningContent": {"redactedContent": "blob-cifrado"}}
        ) is None

    def test_content_event(self):
        assert parse_kiro_event({"content": "hola"}) == {"type": "content", "data": "hola"}

    def test_tool_use_event(self):
        event = parse_kiro_event({"name": "terminal", "toolUseId": "t1", "input": '{"a":1}'})
        assert event["type"] == "toolUse"
        assert event["data"]["name"] == "terminal"
        assert event["data"]["input"] == '{"a":1}'

    def test_tool_use_with_dict_input_is_serialized(self):
        event = parse_kiro_event({"name": "read", "toolUseId": "t2", "input": {"path": "a"}})
        assert json.loads(event["data"]["input"]) == {"path": "a"}

    def test_tool_use_input_event(self):
        assert parse_kiro_event({"input": '{"b":2}'}) == {
            "type": "toolUseInput",
            "data": {"input": '{"b":2}'},
        }

    def test_tool_use_stop_event(self):
        assert parse_kiro_event({"stop": True})["type"] == "toolUseStop"

    def test_context_usage_is_not_confused_with_stop(self):
        event = parse_kiro_event({"stop": True, "contextUsagePercentage": 12.5})
        assert event["type"] == "contextUsage"

    def test_error_event_in_both_casings(self):
        assert parse_kiro_event({"error": "Boom", "message": "detalle"})["type"] == "error"
        assert parse_kiro_event({"Error": "Boom", "Message": "detalle"})["type"] == "error"

    def test_usage_event(self):
        event = parse_kiro_event({"usage": {"inputTokens": 10, "outputTokens": 4}})
        assert event["data"] == {"inputTokens": 10, "outputTokens": 4}

    def test_non_dict_usage_yields_none(self):
        # El runtime ha enviado floats aquí en producción; no debe reventar.
        assert parse_kiro_event({"usage": 42.0}) is None

    def test_unknown_and_non_dict_payloads(self):
        assert parse_kiro_event({"algo": "raro"}) is None
        assert parse_kiro_event(42.0) is None


class TestBracketTools:
    def test_single_call_is_extracted_and_removed(self):
        text = 'Antes [Called terminal with args: {"command": "pwd"}] despues'
        calls, cleaned = parse_bracket_tool_calls(text)
        assert len(calls) == 1
        assert calls[0]["function"]["name"] == "terminal"
        assert json.loads(calls[0]["function"]["arguments"]) == {"command": "pwd"}
        assert cleaned == "Antes  despues"

    def test_multiple_calls(self):
        text = (
            '[Called a with args: {"x": 1}] medio [Called b with args: {"y": 2}]'
        )
        calls, cleaned = parse_bracket_tool_calls(text)
        assert [call["function"]["name"] for call in calls] == ["a", "b"]
        assert cleaned.strip() == "medio"

    def test_nested_objects_are_delimited_by_brace_counting(self):
        text = '[Called f with args: {"outer": {"inner": [1, 2]}}]'
        calls, _ = parse_bracket_tool_calls(text)
        assert json.loads(calls[0]["function"]["arguments"]) == {"outer": {"inner": [1, 2]}}

    def test_text_without_calls_is_untouched(self):
        assert parse_bracket_tool_calls("solo texto") == ([], "solo texto")

    def test_malformed_json_is_ignored(self):
        text = '[Called f with args: {no es json}]'
        calls, cleaned = parse_bracket_tool_calls(text)
        assert calls == []
        assert cleaned == text

    def test_trailing_content_before_bracket_is_rejected(self):
        text = '[Called f with args: {"a": 1} basura]'
        assert parse_bracket_tool_calls(text)[0] == []

    def test_generated_ids_are_unique(self):
        text = '[Called a with args: {}] [Called a with args: {}]'
        calls, _ = parse_bracket_tool_calls(text)
        assert calls[0]["id"] != calls[1]["id"]


class TestKiroTools:
    def test_openai_function_becomes_tool_specification(self):
        tools = convert_tools_to_kiro([
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Ejecuta",
                    "parameters": {"type": "object"},
                },
            }
        ])
        assert tools == [
            {
                "toolSpecification": {
                    "name": "terminal",
                    "description": "Ejecuta",
                    "inputSchema": {"json": {"type": "object"}},
                }
            }
        ]

    def test_non_function_tools_are_skipped(self):
        assert convert_tools_to_kiro([{"type": "retrieval"}]) == []

    def test_placeholders_are_added_for_history_only_tools(self):
        history = [{"assistantResponseMessage": {"toolUses": [{"name": "antigua"}]}}]
        result = add_placeholder_tools([], history)
        assert [tool["toolSpecification"]["name"] for tool in result] == ["antigua"]

    def test_already_declared_tools_are_not_duplicated(self):
        declared = [{"toolSpecification": {"name": "antigua", "description": "real"}}]
        history = [{"assistantResponseMessage": {"toolUses": [{"name": "antigua"}]}}]
        assert add_placeholder_tools(declared, history) == declared

    def test_empty_history_returns_tools_unchanged(self):
        assert add_placeholder_tools([], []) == []


class TestAnthropicRequests:
    def test_text_blocks_are_joined_with_newlines(self):
        content = [{"type": "text", "text": "uno"}, {"type": "text", "text": "dos"}]
        assert anthropic_content_text(content) == "uno\ndos"

    def test_string_content_passes_through(self):
        assert anthropic_content_text("plano") == "plano"

    def test_system_and_user_are_converted(self):
        messages, tools = convert_anthropic_request(
            _Request([{"role": "user", "content": "hola"}], system="Eres un agente.")
        )
        assert messages[0] == {"role": "system", "content": "Eres un agente."}
        assert messages[1] == {"role": "user", "content": "hola"}
        assert tools == []

    def test_tool_use_becomes_tool_calls(self):
        messages, _ = convert_anthropic_request(
            _Request([
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "voy"},
                        {"type": "tool_use", "id": "toolu_1", "name": "f", "input": {"a": 1}},
                    ],
                }
            ])
        )
        assert messages[0]["tool_calls"][0]["id"] == "toolu_1"
        assert messages[0]["tool_calls"][0]["function"]["arguments"] == '{"a":1}'

    def test_tool_result_becomes_tool_role(self):
        messages, _ = convert_anthropic_request(
            _Request([
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "salida"}
                    ],
                }
            ])
        )
        assert messages[0] == {
            "role": "tool",
            "tool_call_id": "toolu_1",
            "content": "salida",
            "is_error": False,
        }

    def test_failed_tool_result_is_flagged(self):
        messages, _ = convert_anthropic_request(
            _Request([
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "t",
                            "content": "boom",
                            "is_error": True,
                        }
                    ],
                }
            ])
        )
        assert messages[0]["is_error"] is True

    def test_empty_block_list_keeps_the_turn(self):
        messages, _ = convert_anthropic_request(_Request([{"role": "user", "content": []}]))
        assert messages == [{"role": "user", "content": ""}]

    def test_input_schema_becomes_parameters(self):
        _, tools = convert_anthropic_request(
            _Request(
                [{"role": "user", "content": "x"}],
                tools=[{"name": "f", "input_schema": {"type": "object"}}],
            )
        )
        assert tools[0]["function"]["parameters"] == {"type": "object"}

    def test_tool_without_schema_gets_an_empty_object(self):
        _, tools = convert_anthropic_request(
            _Request([{"role": "user", "content": "x"}], tools=[{"name": "f"}])
        )
        assert tools[0]["function"]["parameters"] == {"type": "object", "properties": {}}


class TestAnthropicResponses:
    def test_reasoning_becomes_thinking_block_before_text(self):
        response = anthropic_response_from_openai({
            "choices": [{
                "message": {
                    "reasoning_content": "plan",
                    "content": "respuesta",
                },
                "finish_reason": "stop",
            }]
        })
        assert response["content"] == [
            {"type": "thinking", "thinking": "plan", "signature": ""},
            {"type": "text", "text": "respuesta"},
        ]

    def test_text_only_response(self):
        response = anthropic_response_from_openai({
            "id": "chatcmpl-1",
            "model": "claude-sonnet-5",
            "choices": [{"message": {"content": "hola"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        })
        assert response["content"] == [{"type": "text", "text": "hola"}]
        assert response["stop_reason"] == "end_turn"
        assert response["usage"] == {"input_tokens": 5, "output_tokens": 2}

    def test_tool_calls_become_tool_use_and_change_stop_reason(self):
        response = anthropic_response_from_openai({
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "f", "arguments": '{"a":1}'},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        })
        assert response["content"][0]["input"] == {"a": 1}
        assert response["stop_reason"] == "tool_use"

    def test_unparseable_arguments_are_preserved_as_raw(self):
        response = anthropic_response_from_openai({
            "choices": [
                {
                    "message": {
                        "tool_calls": [{"function": {"name": "f", "arguments": "{roto"}}]
                    }
                }
            ]
        })
        assert response["content"][0]["input"] == {"raw": "{roto"}

    def test_token_estimations(self):
        assert estimate_tokens(0) == 1
        assert estimate_tokens(40) == 10
        assert estimate_tokens(40, image_count=2) == 3210
        assert estimate_output_tokens(1) == 1
        assert estimate_output_tokens(4) == 1
        assert estimate_output_tokens(5) == 2

    def test_sse_frame_format(self):
        frame = anthropic_sse("message_stop", {"type": "message_stop"})
        assert frame == 'event: message_stop\ndata: {"type":"message_stop"}\n\n'


class TestContinuation:
    @pytest.mark.parametrize(
        "text",
        [
            "Ahora lo voy a corregir",
            "I will fix it",
            "Next I'll run the tests",
            "Lo ejecutaré ya",
            "Corregiré el fichero",
            "Buscaré el archivo exacto.",
            "Leeré la plantilla y mostraré el resultado.",
            "Pruebo el marcador. Mediré ambos canales.",
        ],
    )
    def test_promises_are_detected(self, text):
        assert response_promises_followup(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "Listo, ya está.",
            "El fichero tiene 10 líneas.",
            "",
            # La rama con adverbio exige la raíz del verbo, no una forma conjugada.
            "Ahora lo corrijo",
        ],
    )
    def test_completed_answers_are_not_promises(self, text):
        assert response_promises_followup(text) is False

    def test_state_with_tool_results_is_detected(self):
        state = {
            "conversationState": {
                "currentMessage": {
                    "userInputMessage": {
                        "userInputMessageContext": {"toolResults": [{"toolUseId": "t"}]}
                    }
                }
            }
        }
        assert state_has_tool_results(state) is True
        assert state_has_tool_results({"conversationState": {}}) is False

    def test_continuation_moves_current_message_into_history(self):
        state = {
            "conversationState": {
                "currentMessage": {
                    "userInputMessage": {
                        "content": "resultado",
                        "modelId": "claude-sonnet-5",
                        "origin": "KIRO_CLI",
                        "images": [{"format": "png"}],
                        "userInputMessageContext": {
                            "toolResults": [{"toolUseId": "t"}],
                            "tools": [{"toolSpecification": {"name": "f"}}],
                        },
                    }
                }
            }
        }
        continued = build_continuation_state(state, "Ahora lo corrijo")
        conversation = continued["conversationState"]

        assert len(conversation["history"]) == 2
        assert conversation["history"][1]["assistantResponseMessage"]["content"] == (
            "Ahora lo corrijo"
        )
        current = conversation["currentMessage"]["userInputMessage"]
        assert current["content"] == AUTO_CONTINUATION_PROMPT
        assert current["userInputMessageContext"]["tools"] == [
            {"toolSpecification": {"name": "f"}}
        ]
        # Las imágenes no se repiten en el historial.
        assert "images" not in conversation["history"][0]["userInputMessage"]
        # El estado original no se muta.
        assert state["conversationState"]["currentMessage"]["userInputMessage"]["content"] == (
            "resultado"
        )


class TestOpenAIModels:
    def test_kiro_catalog_is_translated(self):
        models = to_openai_model_list(
            [
                {
                    "modelId": "claude-sonnet-5",
                    "description": "Sonnet",
                    "tokenLimits": {"maxInputTokens": 1000, "maxOutputTokens": 500},
                    "rateMultiplier": 2.0,
                }
            ],
            created=1700000000,
        )
        assert models == [
            {
                "id": "claude-sonnet-5",
                "object": "model",
                "created": 1700000000,
                "owned_by": "kiro",
                "root": "claude-sonnet-5",
                "max_input_tokens": 1000,
                "max_output_tokens": 500,
                "description": "Sonnet",
                "rate_multiplier": 2.0,
            }
        ]

    def test_defaults_are_applied_when_limits_are_absent(self):
        model = to_openai_model_list([{"modelId": "auto"}], created=0)[0]
        assert model["max_input_tokens"] == 200000
        assert model["max_output_tokens"] == 64000
        assert model["rate_multiplier"] == 1.0

    def test_models_without_id_are_dropped(self):
        assert to_openai_model_list([{"description": "sin id"}], created=0) == []

    def test_fallback_catalog_is_translatable(self):
        assert len(to_openai_model_list(FALLBACK_MODELS, created=0)) == len(FALLBACK_MODELS)

    def test_fallback_model_ids_are_reachable_through_the_alias_table(self):
        # Regresion: el fallback anunciaba "claude-sonnet-4.6"/"claude-opus-4.8",
        # IDs que normalize_model_id no reconoce y que el runtime de Kiro
        # rechaza con INVALID_MODEL_ID. Cada modelId del fallback (salvo "auto",
        # que no pasa por alias) debe ser un valor de destino de MODEL_ALIASES o
        # quedar intacto al normalizarlo.
        from gateway.domain.model_id import MODEL_ALIASES, normalize_model_id

        alias_targets = set(MODEL_ALIASES.values())
        for model in FALLBACK_MODELS:
            model_id = model["modelId"]
            if model_id == "auto":
                continue
            assert model_id in alias_targets or normalize_model_id(model_id) == model_id
