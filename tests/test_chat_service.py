"""Tests de `gateway.application.chat` y los presenters de `gateway.protocols`.

Verifican el flujo unificado que sustituye a las tres implementaciones que
decodificaban el stream de Kiro por separado.
"""

import json

import pytest

from gateway.application.chat import ChatService
from gateway.domain.errors import UpstreamError
from gateway.domain.events import (
    Completed,
    StreamError,
    TextDelta,
    ThinkingDelta,
    ToolUseDelta,
    ToolUseStart,
    UsageReport,
)
from gateway.protocols.openai.non_stream_presenter import openai_non_stream
from gateway.protocols.openai.sse_presenter import openai_stream


class _FakeRuntimeClient:
    def __init__(self, events):
        self._events = events

    def stream(self, *, access_token, state):
        async def generator():
            for event in self._events:
                yield event

        return generator()


class _FailingRuntimeClient:
    def __init__(self, error: UpstreamError):
        self._error = error

    def stream(self, *, access_token, state):
        async def generator():
            raise self._error
            yield  # pragma: no cover - hace de esta una función generadora

        return generator()


class _SequencedRuntimeClient:
    """Una secuencia por intento; cada elemento es un evento o una excepción."""

    def __init__(self, attempts):
        self._attempts = list(attempts)
        self.calls = 0

    def stream(self, *, access_token, state):
        attempt = self._attempts[self.calls]
        self.calls += 1

        async def generator():
            for item in attempt:
                if isinstance(item, Exception):
                    raise item
                yield item

        return generator()


async def _no_delay(_seconds):
    return None


async def collect(service, **kwargs):
    return [event async for event in service.run(**kwargs)]


class TestChatService:
    async def test_readable_reasoning_is_propagated(self):
        raw = [
            {"reasoningContent": {"text": "plan", "signature": "firma"}},
            {"content": "respuesta"},
        ]
        events = await collect(
            ChatService(_FakeRuntimeClient(raw)), access_token="t", state={}
        )
        assert events == [
            ThinkingDelta("plan", "firma"),
            TextDelta("respuesta"),
            Completed("stop"),
        ]

    async def test_text_only_response(self):
        service = ChatService(_FakeRuntimeClient([{"content": "hola"}, {"content": " mundo"}]))
        events = await collect(service, access_token="t", state={})
        assert events == [TextDelta("hola"), TextDelta(" mundo"), Completed("stop")]

    async def test_tool_use_fragmented_across_events(self):
        raw = [
            {"name": "terminal", "toolUseId": "t1", "input": ""},
            {"name": "terminal", "toolUseId": "t1", "input": '{"command"'},
            {"name": "terminal", "toolUseId": "t1", "input": ': "pwd"}'},
            {"stop": True},
        ]
        events = await collect(ChatService(_FakeRuntimeClient(raw)), access_token="t", state={})
        assert events[0] == ToolUseStart(0, "t1", "terminal", "")
        assert events[1] == ToolUseDelta(0, '{"command"')
        assert events[2] == ToolUseDelta(0, ': "pwd"}')
        assert events[-1] == Completed("tool_calls")

    async def test_tool_use_input_follows_the_active_tool(self):
        raw = [
            {"name": "read", "toolUseId": "t1", "input": ""},
            {"input": '{"path"'},
            {"input": ': "a"}'},
        ]
        events = await collect(ChatService(_FakeRuntimeClient(raw)), access_token="t", state={})
        assert events == [
            ToolUseStart(0, "t1", "read", ""),
            ToolUseDelta(0, '{"path"'),
            ToolUseDelta(0, ': "a"}'),
            Completed("tool_calls"),
        ]

    async def test_two_tool_calls_get_increasing_indices(self):
        raw = [
            {"name": "read", "toolUseId": "a", "input": '{"path":"a"}'},
            {"name": "read", "toolUseId": "b", "input": '{"path":"b"}'},
        ]
        events = await collect(ChatService(_FakeRuntimeClient(raw)), access_token="t", state={})
        starts = [event for event in events if isinstance(event, ToolUseStart)]
        assert [event.index for event in starts] == [0, 1]

    async def test_usage_event(self):
        raw = [{"content": "x"}, {"usage": {"inputTokens": 5, "outputTokens": 2}}]
        events = await collect(ChatService(_FakeRuntimeClient(raw)), access_token="t", state={})
        assert UsageReport(5, 2) in events

    async def test_error_event_is_forwarded_and_stream_continues(self):
        raw = [{"content": "antes"}, {"error": "Boom", "message": "detalle"}, {"content": "despues"}]
        events = await collect(ChatService(_FakeRuntimeClient(raw)), access_token="t", state={})
        assert StreamError("detalle") in events
        assert TextDelta("despues") in events

    async def test_bracket_tool_calls_are_extracted_and_the_text_is_cleaned(self):
        raw = [{"content": 'Ejecutando [Called terminal with args: {"command": "pwd"}] listo'}]
        events = await collect(ChatService(_FakeRuntimeClient(raw)), access_token="t", state={})
        starts = [event for event in events if isinstance(event, ToolUseStart)]
        assert starts[0].name == "terminal"
        assert events[-1] == Completed("tool_calls")
        # La detección es por evento: Kiro manda el bracket completo dentro
        # de un único evento `content`, así que se limpia antes de emitirse
        # y el texto crudo no llega al cliente.
        text_events = [event for event in events if isinstance(event, TextDelta)]
        assert all("[Called" not in event.text for event in text_events)
        assert TextDelta("Ejecutando  listo") in events

    async def test_upstream_error_becomes_a_stream_error_and_completes(self):
        service = ChatService(_FailingRuntimeClient(UpstreamError("caido", status_code=502)))
        events = await collect(service, access_token="t", state={})
        assert events == [StreamError("caido"), Completed("stop")]

    async def test_retryable_stream_exception_is_retried_once_before_output(self, caplog):
        transient = UpstreamError(
            "unexpected error, please try again",
            status_code=502,
            retryable=True,
            request_id="request-123",
        )
        runtime = _SequencedRuntimeClient([[transient], [{"content": "recuperado"}]])
        state = {
            "conversationState": {
                "currentMessage": {"userInputMessage": {"modelId": "claude-sonnet-5"}}
            }
        }

        events = await collect(
            ChatService(runtime, sleep=_no_delay), access_token="t", state=state
        )

        assert events == [TextDelta("recuperado"), Completed("stop")]
        assert runtime.calls == 2
        assert "request_id=request-123" in caplog.text
        assert "model=claude-sonnet-5" in caplog.text
        assert "attempt=2/2" in caplog.text

    @pytest.mark.parametrize(
        "first_event",
        [
            {"content": "una vez"},
            {"name": "terminal", "toolUseId": "tool-1", "input": "{}"},
            {"usage": {"inputTokens": 3, "outputTokens": 1}},
        ],
    )
    async def test_retryable_exception_after_output_is_not_retried(self, first_event):
        transient = UpstreamError("temporary", status_code=502, retryable=True)
        runtime = _SequencedRuntimeClient(
            [[first_event, transient], [{"content": "no debe aparecer"}]]
        )

        events = await collect(
            ChatService(runtime, sleep=_no_delay), access_token="t", state={}
        )

        assert runtime.calls == 1
        assert events.count(TextDelta("una vez")) <= 1
        assert TextDelta("no debe aparecer") not in events
        assert StreamError("temporary") in events
        assert events[-1] == Completed(
            "tool_calls" if "toolUseId" in first_event else "stop"
        )

    async def test_retryable_exception_after_thinking_retries_before_public_output(self):
        transient = UpstreamError("temporary", status_code=502, retryable=True)
        runtime = _SequencedRuntimeClient([
            [{"reasoningContent": {"text": "descartado", "signature": "s1"}}, transient],
            [
                {"reasoningContent": {"text": "válido", "signature": "s2"}},
                {"content": "respuesta"},
            ],
        ])

        events = await collect(
            ChatService(runtime, sleep=_no_delay), access_token="t", state={}
        )

        assert runtime.calls == 2
        assert ThinkingDelta("descartado", "s1") not in events
        assert events == [
            ThinkingDelta("válido", "s2"),
            TextDelta("respuesta"),
            Completed("stop"),
        ]

    async def test_retryable_exception_is_attempted_at_most_twice(self):
        transient = UpstreamError("temporary", status_code=502, retryable=True)
        runtime = _SequencedRuntimeClient([[transient], [transient], [{"content": "third"}]])

        events = await collect(
            ChatService(runtime, sleep=_no_delay), access_token="t", state={}
        )

        assert runtime.calls == 2
        assert events == [StreamError("temporary"), Completed("stop")]

    async def test_non_retryable_exception_is_not_retried(self):
        failure = UpstreamError("invalid payload", status_code=502, retryable=False)
        runtime = _SequencedRuntimeClient([[failure], [{"content": "second"}]])

        events = await collect(
            ChatService(runtime, sleep=_no_delay), access_token="t", state={}
        )

        assert runtime.calls == 1
        assert events == [StreamError("invalid payload"), Completed("stop")]

    async def test_empty_stream_completes_without_tool_calls(self):
        events = await collect(ChatService(_FakeRuntimeClient([])), access_token="t", state={})
        assert events == [Completed("stop")]


async def _events_to_stream(events):
    for event in events:
        yield event


class TestOpenAIStreamPresenter:
    async def _lines(self, events):
        return [line async for line in openai_stream(_events_to_stream(events), "claude-sonnet-5")]

    async def test_text_delta_becomes_a_content_chunk(self):
        lines = await self._lines([TextDelta("hola"), Completed("stop")])
        first = json.loads(lines[0].removeprefix("data: ").strip())
        assert first["choices"][0]["delta"]["content"] == "hola"

    async def test_thinking_delta_becomes_reasoning_content_chunk(self):
        lines = await self._lines([ThinkingDelta("plan"), Completed("stop")])
        first = json.loads(lines[0].removeprefix("data: ").strip())
        assert first["choices"][0]["delta"] == {"reasoning_content": "plan"}

    async def test_completed_emits_usage_and_done(self):
        lines = await self._lines([UsageReport(3, 1), Completed("stop")])
        final = json.loads(lines[-2].removeprefix("data: ").strip())
        assert final["usage"] == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
        assert lines[-1] == "data: [DONE]\n\n"

    async def test_tool_use_start_and_delta(self):
        lines = await self._lines([
            ToolUseStart(0, "call_1", "terminal", '{"a"'),
            ToolUseDelta(0, ":1}"),
            Completed("tool_calls"),
        ])
        payloads = [json.loads(line.removeprefix("data: ").strip()) for line in lines[:-2]]
        first_delta = payloads[0]["choices"][0]["delta"]["tool_calls"][0]
        assert first_delta == {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "terminal", "arguments": '{"a"'},
        }
        second_delta = payloads[1]["choices"][0]["delta"]["tool_calls"][0]
        assert second_delta == {"index": 0, "function": {"arguments": ":1}"}}

    async def test_stream_error_is_forwarded_as_content(self):
        lines = await self._lines([StreamError("Boom"), Completed("stop")])
        payload = json.loads(lines[0].removeprefix("data: ").strip())
        assert "[Kiro error: Boom]" in payload["choices"][0]["delta"]["content"]


class TestOpenAINonStreamPresenter:
    async def test_thinking_only_has_reasoning_and_null_content(self):
        response = await openai_non_stream(
            _events_to_stream([ThinkingDelta("plan"), Completed("stop")]),
            "claude-opus-4.8",
        )
        message = response["choices"][0]["message"]
        assert message["reasoning_content"] == "plan"
        assert message["content"] is None

    async def test_aggregates_text_and_usage(self):
        response = await openai_non_stream(
            _events_to_stream([TextDelta("ho"), TextDelta("la"), UsageReport(4, 2), Completed("stop")]),
            "claude-sonnet-5",
        )
        assert response["choices"][0]["message"]["content"] == "hola"
        assert response["choices"][0]["finish_reason"] == "stop"
        assert response["usage"]["total_tokens"] == 6

    async def test_tool_calls_are_accumulated_by_index(self):
        response = await openai_non_stream(
            _events_to_stream([
                ToolUseStart(0, "call_1", "f", '{"a"'),
                ToolUseDelta(0, ":1}"),
                Completed("tool_calls"),
            ]),
            "claude-sonnet-5",
        )
        tool_call = response["choices"][0]["message"]["tool_calls"][0]
        assert tool_call["function"]["arguments"] == '{"a":1}'

    async def test_content_is_used_as_is_since_chat_service_already_cleans_it(self):
        # openai_non_stream ya no limpia el texto: ChatService lo hace por
        # evento antes de emitirlo. Aquí se simula el TextDelta ya limpio, tal
        # como llegaría en el flujo real.
        response = await openai_non_stream(
            _events_to_stream([
                TextDelta("Ejecutando  listo"),
                ToolUseStart(0, "call_generado", "terminal", '{"command": "pwd"}'),
                Completed("tool_calls"),
            ]),
            "claude-sonnet-5",
        )
        assert response["choices"][0]["message"]["content"] == "Ejecutando  listo"

    async def test_content_without_tool_calls_is_none_when_empty(self):
        response = await openai_non_stream(_events_to_stream([Completed("stop")]), "claude-sonnet-5")
        assert response["choices"][0]["message"]["content"] is None
