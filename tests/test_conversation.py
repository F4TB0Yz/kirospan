"""Tests unitarios del constructor de estado de conversación de Kiro.

Complementan los snapshots: aquí se verifica cada regla del protocolo por
separado, con un `conversation_id` fijo para que la salida sea determinista.
"""

import pytest

from gateway.domain.errors import InvalidRequest
from gateway.protocols.kiro.conversation import (
    DEFAULT_GREETING,
    IMAGE_ONLY_CONTENT,
    PROCEED_PROMPT,
    TOOL_CONTINUATION_PROMPT,
    TOOL_RESULTS_NOTE,
    assistant_response,
    build_conversation_state,
    content_text,
    tool_result,
)

FIXED_ID = "00000000-0000-0000-0000-000000000000"
PNG_DATA_URL = "data:image/png;base64,aGVsbG8="


def build(messages, **kwargs):
    kwargs.setdefault("conversation_id", FIXED_ID)
    return build_conversation_state("claude-sonnet-5", messages, **kwargs)


def conversation_of(state):
    return state["conversationState"]


def current_of(state):
    return conversation_of(state)["currentMessage"]["userInputMessage"]


def history_of(state):
    return conversation_of(state).get("history", [])


class TestContentFlattening:
    def test_string_content(self):
        assert content_text({"content": "hola"}) == "hola"

    def test_blocks_are_joined_without_separator(self):
        # A diferencia del aplanado de Anthropic, que une con saltos de línea.
        message = {"content": [{"type": "text", "text": "uno"}, {"type": "text", "text": "dos"}]}
        assert content_text(message) == "unodos"

    def test_output_text_blocks_are_accepted(self):
        assert content_text({"content": [{"type": "output_text", "text": "x"}]}) == "x"

    def test_non_text_blocks_are_skipped(self):
        message = {"content": [{"type": "image"}, {"type": "text", "text": "solo"}]}
        assert content_text(message) == "solo"

    def test_none_and_scalar_content(self):
        assert content_text({"content": None}) == ""
        assert content_text({"content": 42}) == "42"


class TestAssistantResponse:
    def test_tool_calls_become_tool_uses(self):
        response = assistant_response({
            "content": "voy",
            "tool_calls": [
                {"id": "c1", "function": {"name": "f", "arguments": '{"a":1}'}},
            ],
        })
        assert response["toolUses"] == [{"name": "f", "toolUseId": "c1", "input": {"a": 1}}]

    def test_malformed_arguments_become_an_empty_object(self):
        response = assistant_response({
            "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": "{roto"}}]
        })
        assert response["toolUses"][0]["input"] == {}

    def test_dict_arguments_pass_through(self):
        response = assistant_response({
            "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": {"a": 1}}}]
        })
        assert response["toolUses"][0]["input"] == {"a": 1}

    def test_message_without_tool_calls_has_no_tool_uses_key(self):
        assert "toolUses" not in assistant_response({"content": "texto"})


class TestToolResult:
    def test_successful_result(self):
        assert tool_result({"tool_call_id": "t1", "content": "salida"}) == {
            "content": [{"text": "salida"}],
            "status": "success",
            "toolUseId": "t1",
        }

    def test_failed_result(self):
        assert tool_result({"tool_call_id": "t", "content": "boom", "is_error": True})[
            "status"
        ] == "error"


class TestSystemPrompt:
    def test_system_role_is_prepended_to_first_user_message(self):
        state = build([
            {"role": "system", "content": "Eres un agente."},
            {"role": "user", "content": "hola"},
        ])
        assert current_of(state)["content"] == "Eres un agente.\n\nhola"

    def test_system_prompt_argument_and_system_role_are_concatenated(self):
        state = build(
            [
                {"role": "system", "content": "Del mensaje."},
                {"role": "user", "content": "hola"},
            ],
            system_prompt="Del argumento.",
        )
        assert current_of(state)["content"] == "Del argumento.\n\nDel mensaje.\n\nhola"

    def test_system_goes_to_the_first_user_of_the_history(self):
        state = build([
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "primero"},
            {"role": "assistant", "content": "respuesta"},
            {"role": "user", "content": "segundo"},
        ])
        assert history_of(state)[0]["userInputMessage"]["content"] == "SYS\n\nprimero"
        assert current_of(state)["content"] == "segundo"

    def test_empty_system_content_is_ignored(self):
        state = build([{"role": "system", "content": ""}, {"role": "user", "content": "hola"}])
        assert current_of(state)["content"] == "hola"


class TestAlternatingHistory:
    def test_consecutive_users_are_merged(self):
        state = build([
            {"role": "user", "content": "uno"},
            {"role": "user", "content": "dos"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "tres"},
        ])
        assert history_of(state)[0]["userInputMessage"]["content"] == "uno\n\ndos"
        assert "assistantResponseMessage" in history_of(state)[1]

    def test_history_alternates_strictly(self):
        state = build([
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
            {"role": "user", "content": "u3"},
        ])
        keys = [next(iter(entry)) for entry in history_of(state)]
        assert keys == [
            "userInputMessage",
            "assistantResponseMessage",
            "userInputMessage",
            "assistantResponseMessage",
        ]

    def test_empty_assistant_messages_are_dropped(self):
        state = build([
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "u2"},
        ])
        # El asistente vacío no llega al historial, que queda solo con u1;
        # u2 es el último mensaje y viaja en currentMessage.
        assert len(history_of(state)) == 1
        assert history_of(state)[0]["userInputMessage"]["content"] == "u1"
        assert current_of(state)["content"] == "u2"

    def test_consecutive_users_inside_the_history_are_merged(self):
        state = build([
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "u3"},
        ])
        assert history_of(state)[0]["userInputMessage"]["content"] == "u1\n\nu2"
        assert current_of(state)["content"] == "u3"

    def test_no_messages_produces_a_default_greeting(self):
        state = build([])
        assert current_of(state)["content"] == DEFAULT_GREETING
        assert history_of(state) == []

    def test_history_ending_in_assistant_asks_to_proceed(self):
        state = build([
            {"role": "user", "content": "pregunta"},
            {"role": "assistant", "content": "respuesta"},
        ])
        assert current_of(state)["content"] == PROCEED_PROMPT


class TestToolResults:
    def test_tool_results_travel_in_the_current_message(self):
        state = build([
            {"role": "user", "content": "ejecuta"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "salida"},
        ])
        current = current_of(state)
        assert current["content"] == TOOL_RESULTS_NOTE
        assert current["userInputMessageContext"]["toolResults"][0]["toolUseId"] == "c1"

    def test_parallel_tool_results_are_grouped(self):
        state = build([
            {"role": "user", "content": "dos cosas"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "a", "function": {"name": "f", "arguments": "{}"}},
                    {"id": "b", "function": {"name": "f", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "a", "content": "ra"},
            {"role": "tool", "tool_call_id": "b", "content": "rb"},
        ])
        results = current_of(state)["userInputMessageContext"]["toolResults"]
        assert [result["toolUseId"] for result in results] == ["a", "b"]

    def test_historic_tool_results_hang_from_the_previous_user(self):
        state = build([
            {"role": "user", "content": "turno uno"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "salida"},
            {"role": "assistant", "content": "listo"},
            {"role": "user", "content": "turno dos"},
        ])
        # El rol tool no existe en Kiro: se convierte en nota + contexto del user.
        user_entries = [
            entry["userInputMessage"]
            for entry in history_of(state)
            if "userInputMessage" in entry
        ]
        with_results = [entry for entry in user_entries if "userInputMessageContext" in entry]
        assert len(with_results) == 1
        assert TOOL_RESULTS_NOTE in with_results[0]["content"]

    def test_continuation_prompt_is_appended_only_with_tool_results(self):
        messages = [
            {"role": "user", "content": "ejecuta"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "f", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "salida"},
        ]
        forced = build(messages, enforce_tool_continuation=True)
        assert current_of(forced)["content"].endswith(TOOL_CONTINUATION_PROMPT)

        plain = build([{"role": "user", "content": "hola"}], enforce_tool_continuation=True)
        assert TOOL_CONTINUATION_PROMPT not in current_of(plain)["content"]


class TestTools:
    def test_declared_tools_go_in_the_context(self):
        state = build(
            [{"role": "user", "content": "hola"}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "f", "description": "d", "parameters": {}},
                }
            ],
        )
        tools = current_of(state)["userInputMessageContext"]["tools"]
        assert tools[0]["toolSpecification"]["name"] == "f"

    def test_history_tools_are_redeclared_as_placeholders(self):
        state = build([
            {"role": "user", "content": "u1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c", "function": {"name": "antigua", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c", "content": "r"},
            {"role": "assistant", "content": "listo"},
            {"role": "user", "content": "u2"},
        ])
        names = {
            tool["toolSpecification"]["name"]
            for tool in current_of(state)["userInputMessageContext"]["tools"]
        }
        assert "antigua" in names

    def test_without_tools_there_is_no_context(self):
        assert "userInputMessageContext" not in current_of(build([{"role": "user", "content": "x"}]))


class TestImages:
    def test_image_is_attached_to_the_current_message(self):
        state = build([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": PNG_DATA_URL}},
                ],
            }
        ])
        assert current_of(state)["images"][0]["format"] == "png"

    def test_image_without_text_gets_placeholder_content(self):
        state = build([
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}}]}
        ])
        assert current_of(state)["content"] == IMAGE_ONLY_CONTENT

    def test_history_images_are_not_repeated(self):
        state = build([
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}}],
            },
            {"role": "assistant", "content": "es un png"},
            {"role": "user", "content": "gracias"},
        ])
        assert "images" not in current_of(state)
        assert "images" not in history_of(state)[0]["userInputMessage"]

    def test_too_many_images_are_rejected(self):
        blocks = [{"type": "image_url", "image_url": {"url": PNG_DATA_URL}} for _ in range(11)]
        with pytest.raises(InvalidRequest, match="at most 10 images"):
            build([{"role": "user", "content": blocks}])


class TestStateEnvelope:
    def test_fixed_fields(self):
        conversation = conversation_of(build([{"role": "user", "content": "hola"}]))
        assert conversation["chatTriggerType"] == "MANUAL"
        assert conversation["agentTaskType"] == "vibe"
        assert conversation["conversationId"] == FIXED_ID

    def test_model_id_and_origin_are_propagated(self):
        current = current_of(build([{"role": "user", "content": "hola"}]))
        assert current["modelId"] == "claude-sonnet-5"
        assert current["origin"] == "KIRO_CLI"

    def test_conversation_id_defaults_to_the_stable_process_id(self):
        from gateway.domain.session import CONVERSATION_ID

        state = build_conversation_state("auto", [{"role": "user", "content": "x"}])
        assert conversation_of(state)["conversationId"] == CONVERSATION_ID
