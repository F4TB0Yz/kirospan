"""Pruebas del control de reasoning enviado al runtime de Kiro."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from gateway.adapters.inbound.http.routers.anthropic import build_anthropic_router
from gateway.adapters.inbound.http.routers.chat import build_chat_router
from gateway.adapters.inbound.http.schemas import AnthropicRequest, ChatRequest
from gateway.protocols.kiro.reasoning import (
    build_additional_model_request_fields,
    thinking_system_markers,
)


class _TokenService:
    async def ensure(self):
        return {
            "access_token": "token",
            "profile_arn": "arn:aws:codewhisperer:us-east-1:test:profile/test",
        }


class _UnusedCatalog:
    async def fetch(self, _token):
        raise AssertionError("El token de prueba ya contiene profile_arn")


class _CapturingRuntime:
    def __init__(self):
        self.states = []

    async def stream(self, *, access_token, state):
        self.states.append(state)
        yield {"content": "respuesta"}


class _ReasoningRuntime(_CapturingRuntime):
    async def stream(self, *, access_token, state):
        self.states.append(state)
        yield {"reasoningContent": {"text": "plan", "signature": "firma"}}
        yield {"content": "respuesta"}


def _client_with(router_builder):
    runtime = _CapturingRuntime()
    app = FastAPI()
    app.include_router(
        router_builder(_TokenService(), _UnusedCatalog(), lambda: runtime)
    )
    return TestClient(app), runtime


def _client_with_reasoning(router_builder):
    runtime = _ReasoningRuntime()
    app = FastAPI()
    app.include_router(
        router_builder(_TokenService(), _UnusedCatalog(), lambda: runtime)
    )
    return TestClient(app), runtime


def test_openai_schema_preserves_reasoning_effort():
    request = ChatRequest(model="claude-opus-4.8", messages=[], reasoning_effort="high")
    assert request.reasoning_effort == "high"


def test_anthropic_schema_preserves_thinking_and_output_config():
    request = AnthropicRequest(
        model="claude-opus-4.8",
        messages=[],
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
    )
    assert request.thinking == {"type": "adaptive"}
    assert request.output_config == {"effort": "high"}


def test_modern_claude_requests_visible_summarized_thinking():
    assert build_additional_model_request_fields(
        "claude-opus-4.8", reasoning_effort="high"
    ) == {
        "output_config": {"effort": "high"},
        "thinking": {"type": "adaptive", "display": "summarized"},
    }


def test_claude_high_effort_gets_confirmed_kiro_thinking_markers():
    fields = build_additional_model_request_fields(
        "claude-opus-4.8", reasoning_effort="high"
    )
    assert thinking_system_markers(fields) == (
        "<thinking_mode>enabled</thinking_mode>"
        "<max_thinking_length>30000</max_thinking_length>"
    )


def test_claude_46_clamps_xhigh_and_omits_unsupported_display():
    assert build_additional_model_request_fields(
        "claude-sonnet-4.6", reasoning_effort="xhigh"
    ) == {
        "output_config": {"effort": "max"},
        "thinking": {"type": "adaptive"},
    }


def test_normalized_1m_models_use_base_model_reasoning_capabilities():
    assert build_additional_model_request_fields(
        "claude-sonnet-5.1m", reasoning_effort="high"
    ) == {
        "output_config": {"effort": "high"},
        "thinking": {"type": "adaptive", "display": "summarized"},
    }
    assert build_additional_model_request_fields(
        "claude-opus-4.6.1m", reasoning_effort="xhigh"
    ) == {
        "output_config": {"effort": "max"},
        "thinking": {"type": "adaptive"},
    }


def test_gpt_uses_reasoning_field_and_never_claims_visible_thinking():
    assert build_additional_model_request_fields(
        "gpt-5.6-sol", reasoning_effort="high"
    ) == {"reasoning": {"effort": "high"}}


def test_anthropic_thinking_without_effort_defaults_to_medium():
    assert build_additional_model_request_fields(
        "claude-sonnet-5", thinking={"type": "enabled", "budget_tokens": 16000}
    ) == {
        "output_config": {"effort": "medium"},
        "thinking": {"type": "adaptive", "display": "summarized"},
    }


def test_disabled_or_missing_reasoning_omits_upstream_field():
    assert build_additional_model_request_fields("claude-opus-4.8") is None
    assert (
        build_additional_model_request_fields(
            "claude-opus-4.8",
            thinking={"type": "disabled"},
            output_config={"effort": "high"},
        )
        is None
    )


def test_openai_router_sends_reasoning_at_kiro_payload_root():
    client, runtime = _client_with(build_chat_router)
    with client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "claude-opus-4.8",
                "messages": [{"role": "user", "content": "hola"}],
                "reasoning_effort": "high",
            },
        )

    assert response.status_code == 200
    state = runtime.states[0]
    assert state["additionalModelRequestFields"]["thinking"]["display"] == "summarized"
    assert "additionalModelRequestFields" not in state["conversationState"]
    content = state["conversationState"]["currentMessage"]["userInputMessage"]["content"]
    assert content.startswith("<thinking_mode>enabled</thinking_mode>")


def test_anthropic_router_sends_adaptive_thinking_at_kiro_payload_root():
    client, runtime = _client_with(build_anthropic_router)
    with client:
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4.8",
                "messages": [{"role": "user", "content": "hola"}],
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        )

    assert response.status_code == 200
    state = runtime.states[0]
    assert state["additionalModelRequestFields"] == {
        "output_config": {"effort": "high"},
        "thinking": {"type": "adaptive", "display": "summarized"},
    }
    content = state["conversationState"]["currentMessage"]["userInputMessage"]["content"]
    assert content.startswith("<thinking_mode>enabled</thinking_mode>")


def test_anthropic_stream_includes_thinking_signature():
    client, _runtime = _client_with_reasoning(build_anthropic_router)
    with client:
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4.8",
                "stream": True,
                "messages": [{"role": "user", "content": "hola"}],
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        )

    assert response.status_code == 200
    assert '"delta":{"type":"signature_delta","signature":"firma"}' in response.text


def test_anthropic_non_stream_includes_thinking_signature():
    client, _runtime = _client_with_reasoning(build_anthropic_router)
    with client:
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4.8",
                "messages": [{"role": "user", "content": "hola"}],
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        )

    assert response.status_code == 200
    thinking = response.json()["content"][0]
    assert thinking == {"type": "thinking", "thinking": "plan", "signature": "firma"}


def test_anthropic_stream_closes_thinking_before_starting_text():
    client, _runtime = _client_with_reasoning(build_anthropic_router)
    with client:
        response = client.post(
            "/v1/messages",
            json={
                "model": "claude-opus-4.8",
                "stream": True,
                "messages": [{"role": "user", "content": "hola"}],
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        )

    assert response.status_code == 200
    thinking_delta = '"delta":{"type":"thinking_delta","thinking":"plan"}'
    thinking_stop = '"type":"content_block_stop","index":0'
    text_start = '"index":1,"content_block":{"type":"text"'
    assert thinking_delta in response.text
    assert response.text.index(thinking_delta) < response.text.index(thinking_stop)
    assert response.text.index(thinking_stop) < response.text.index(text_start)
