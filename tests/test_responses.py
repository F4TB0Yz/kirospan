"""Tests unitarios del protocolo Responses API (input + presenters), sin red."""

import json

import pytest

from gateway.domain.events import (
    Completed,
    StreamError,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageReport,
)
from gateway.protocols.responses.input import (
    content_to_text,
    responses_input_to_messages,
    responses_reasoning_effort,
    responses_tools_to_chat,
)
from gateway.protocols.responses.presenter import (
    responses_non_stream,
    responses_stream,
)


class TestResponsesInput:
    def test_string_input_becomes_user_message(self):
        assert responses_input_to_messages("hola") == [
            {"role": "user", "content": "hola"}
        ]

    def test_instructions_are_prepended_as_system_message(self):
        messages = responses_input_to_messages("hola", instructions="Sé breve")
        assert messages[0] == {"role": "system", "content": "Sé breve"}
        assert messages[1] == {"role": "user", "content": "hola"}

    def test_message_items_with_input_text_parts(self):
        result = responses_input_to_messages(
            [
                {"role": "user", "content": [{"type": "input_text", "text": "a"}]},
                {"role": "user", "content": [{"type": "input_text", "text": "b"}]},
            ]
        )
        assert result == [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ]

    def test_function_call_output_maps_to_tool_message(self):
        result = responses_input_to_messages(
            [
                {"type": "function_call_output", "call_id": "call_1", "output": "42"},
                {"type": "function_call_output", "call_id": "call_2", "output": None},
            ]
        )
        assert result == [
            {"role": "tool", "tool_call_id": "call_1", "content": "42"},
            {"role": "tool", "tool_call_id": "call_2", "content": ""},
        ]

    def test_function_call_item_maps_to_assistant_tool_calls(self):
        result = responses_input_to_messages(
            [
                {
                    "type": "function_call",
                    "call_id": "call_x",
                    "name": "get_weather",
                    "arguments": '{"city":"Cali"}',
                }
            ]
        )
        assert result == [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_x",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city":"Cali"}',
                        },
                    }
                ],
            }
        ]

    def test_function_call_with_dict_arguments_is_serialized(self):
        result = responses_input_to_messages(
            [
                {
                    "type": "function_call",
                    "call_id": "call_x",
                    "name": "f",
                    "arguments": {"city": "Cali"},
                }
            ]
        )
        arguments = result[0]["tool_calls"][0]["function"]["arguments"]
        assert json.loads(arguments) == {"city": "Cali"}

    def test_assistant_message_with_function_call_parts(self):
        result = responses_input_to_messages(
            [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "veo."},
                        {
                            "type": "function_call",
                            "call_id": "call_y",
                            "name": "search",
                            "arguments": '{"q":"kiro"}',
                        },
                    ],
                }
            ]
        )
        assert result == [
            {
                "role": "assistant",
                "content": "veo.",
                "tool_calls": [
                    {
                        "id": "call_y",
                        "type": "function",
                        "function": {"name": "search", "arguments": '{"q":"kiro"}'},
                    }
                ],
            }
        ]

    def test_reasoning_items_are_skipped(self):
        result = responses_input_to_messages(
            [
                {"type": "reasoning", "summary": "pensar"},
                {"role": "user", "content": "sigue"},
            ]
        )
        assert result == [{"role": "user", "content": "sigue"}]

    def test_empty_input_returns_empty_messages(self):
        assert responses_input_to_messages([]) == []
        assert responses_input_to_messages("") == []


class TestResponsesTools:
    def test_tool_shape_is_translated(self):
        tools = [
            {
                "type": "function",
                "name": "lookup",
                "description": "Busca",
                "parameters": {"type": "object", "properties": {}},
            },
            {"type": "web_search_preview"},
        ]
        assert responses_tools_to_chat(tools) == [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Busca",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def test_no_tools_returns_none(self):
        assert responses_tools_to_chat(None) is None
        assert responses_tools_to_chat([{"type": "web_search_preview"}]) is None


class TestResponsesReasoningEffort:
    def test_dict_effort(self):
        assert responses_reasoning_effort({"effort": "high"}) == "high"

    def test_string_effort(self):
        assert responses_reasoning_effort("low") == "low"

    def test_none(self):
        assert responses_reasoning_effort(None) is None
        assert responses_reasoning_effort({"summary": "auto"}) is None


async def _fake_events(*events):
    for event in events:
        yield event


class TestResponsesNonStream:
    async def test_plain_text_response(self):
        result = await responses_non_stream(
            _fake_events(
                TextDelta("hola "),
                TextDelta("mundo"),
                UsageReport(10, 5),
                Completed("stop"),
            ),
            "gpt-5.6-sol",
        )
        assert result["object"] == "response"
        assert result["model"] == "gpt-5.6-sol"
        assert result["status"] == "completed"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 5
        assert len(result["output"]) == 1
        item = result["output"][0]
        assert item["type"] == "message"
        assert item["role"] == "assistant"
        assert item["content"] == [
            {"type": "output_text", "text": "hola mundo", "annotations": []}
        ]

    async def test_function_call_response(self):
        result = await responses_non_stream(
            _fake_events(
                ToolUseStart(0, "call_1", "search", '{"q":'),
                ToolUseDelta(0, '"kiro"}'),
                Completed("tool_calls"),
            ),
            "claude-sonnet-5",
        )
        assert len(result["output"]) == 1
        call = result["output"][0]
        assert call["type"] == "function_call"
        assert call["call_id"] == "call_1"
        assert call["name"] == "search"
        assert call["arguments"] == '{"q":"kiro"}'

    async def test_text_and_tool_call(self):
        result = await responses_non_stream(
            _fake_events(TextDelta("ok "), ToolUseStart(0, "c1", "f", "{}"), Completed("tool_calls")),
            "auto",
        )
        types = [item["type"] for item in result["output"]]
        assert types == ["message", "function_call"]

    async def test_upstream_error_marks_failed(self):
        result = await responses_non_stream(
            _fake_events(StreamError("boom"), Completed("stop")),
            "auto",
        )
        assert result["status"] == "failed"
        assert result["error"]["message"] == "boom"


class TestResponsesStream:
    async def _collect(self, *events):
        lines = [line async for line in responses_stream(_fake_events(*events), "auto")]
        parsed = []
        for line in lines:
            event_line, data_line = line.split("\n", 1)
            assert event_line.startswith("event: ")
            assert data_line.startswith("data: ")
            parsed.append((event_line[7:], json.loads(data_line[6:])))
        return parsed

    async def test_event_sequence_for_text(self):
        parsed = await self._collect(TextDelta("hola"), TextDelta(" mundo"), Completed("stop"))
        types = [t for t, _ in parsed]
        assert types[0] == "response.created"
        assert types[1] == "response.in_progress"
        assert types[2] == "response.output_item.added"
        assert types[3] == "response.content_part.added"
        assert types[4] == "response.output_text.delta"
        assert types[5] == "response.output_text.delta"
        assert types[6] == "response.output_text.done"
        assert types[7] == "response.content_part.done"
        assert types[8] == "response.output_item.done"
        assert types[9] == "response.completed"

        deltas = [d["delta"] for t, d in parsed if t == "response.output_text.delta"]
        assert deltas == ["hola", " mundo"]
        done = [d["text"] for t, d in parsed if t == "response.output_text.done"]
        assert done == ["hola mundo"]
        completed = [d for t, d in parsed if t == "response.completed"][0]
        assert completed["response"]["status"] == "completed"

    async def test_function_call_stream(self):
        parsed = await self._collect(
            ToolUseStart(0, "call_1", "search", '{"q":'),
            ToolUseDelta(0, '"kiro"}'),
            Completed("tool_calls"),
        )
        types = [t for t, _ in parsed]
        assert "response.output_item.added" in types
        assert "response.function_call_arguments.delta" in types
        assert "response.function_call_arguments.done" in types
        assert "response.output_item.done" in types
        assert types[-1] == "response.completed"

        added = [
            d["item"] for t, d in parsed if t == "response.output_item.added"
        ]
        assert added[0]["type"] == "function_call"
        assert added[0]["call_id"] == "call_1"
        done = [d["arguments"] for t, d in parsed if t == "response.function_call_arguments.done"]
        assert done == ['{"q":"kiro"}']

    async def test_thinking_events_are_skipped_in_stream(self):
        parsed = await self._collect(
            ThinkingDelta("plan"),
            TextDelta("resp"),
            Completed("stop"),
        )
        types = [t for t, _ in parsed]
        assert "response.output_text.delta" in types
        deltas = [d["delta"] for t, d in parsed if t == "response.output_text.delta"]
        assert deltas == ["resp"]

    async def test_error_stream_marks_failed(self):
        parsed = await self._collect(StreamError("boom"), Completed("stop"))
        types = [t for t, _ in parsed]
        assert "response.failed" in types
        completed = [d for t, d in parsed if t == "response.completed"][0]
        assert completed["response"]["status"] == "failed"


class TestContentToText:
    def test_string_passthrough(self):
        assert content_to_text("x") == "x"

    def test_list_parts_only_text_types(self):
        parts = [
            {"type": "input_text", "text": "a"},
            {"type": "output_text", "text": "b"},
            {"type": "image_url", "image_url": "..."},
        ]
        assert content_to_text(parts) == "ab"

    def test_none_becomes_empty(self):
        assert content_to_text(None) == ""