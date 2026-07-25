"""Regresión contra los snapshots capturados del monolito original.

Estos tests son la red de seguridad de las fases 4 y 8: cualquier diferencia
en la traducción de protocolo o en el stream de eventos aparece aquí como un
fallo, comparando la estructura completa y no solo campos sueltos.
"""

import json
from pathlib import Path

import pytest

import kiro_gateway
from tests import fakes
from tests.cases import ANTHROPIC_REQUEST_CASES, CONVERSATION_CASES, MODEL_ID_CASES
from tests.generate_snapshots import _normalize_sse_lines
from tests.runtime_cases import ANTHROPIC_STREAM_CASES, RUNTIME_CASES

SNAPSHOT_DIR = Path(__file__).resolve().parent / "snapshots"


def load_snapshot(name):
    return json.loads((SNAPSHOT_DIR / f"{name}.json").read_text())


@pytest.fixture
def fixed_events(monkeypatch):
    """Sustituye el lector de EventStream por una secuencia fija."""

    def apply(events):
        monkeypatch.setattr(kiro_gateway, "read_aws_eventstream", fakes.event_reader(events))
        return fakes.FakeRuntimeClient()

    return apply


@pytest.mark.parametrize("case", CONVERSATION_CASES, ids=lambda case: case["name"])
def test_conversation_state_matches_snapshot(case):
    expected = load_snapshot("conversation_state")[case["name"]]
    state = kiro_gateway._build_conversation_state(
        case["model_id"],
        case["messages"],
        system_prompt=case["system_prompt"],
        tools=case["tools"],
        enforce_tool_continuation=case["enforce_tool_continuation"],
    )
    assert fakes.normalize(state) == expected


@pytest.mark.parametrize("case", ANTHROPIC_REQUEST_CASES, ids=lambda case: case["name"])
def test_anthropic_request_conversion_matches_snapshot(case):
    expected = load_snapshot("anthropic_requests")[case["name"]]
    request = kiro_gateway.AnthropicRequest(**case["request"])
    messages, tools = kiro_gateway._convert_anthropic_request(request)
    assert fakes.normalize({"messages": messages, "tools": tools}) == expected


@pytest.mark.parametrize("model_id", MODEL_ID_CASES)
def test_model_id_normalization_matches_snapshot(model_id):
    assert kiro_gateway._normalize_model_id(model_id) == load_snapshot("model_ids")[model_id]


@pytest.mark.parametrize("case", RUNTIME_CASES, ids=lambda case: case["name"])
async def test_openai_stream_matches_snapshot(case, fixed_events):
    expected = load_snapshot("openai_stream")[case["name"]]
    client = fixed_events(case["events"])
    lines = [
        line
        async for line in kiro_gateway._stream_runtime(
            client, "access-token", {"conversationState": {}}, "claude-sonnet-5"
        )
    ]
    assert _normalize_sse_lines(lines) == expected


@pytest.mark.parametrize("case", RUNTIME_CASES, ids=lambda case: case["name"])
async def test_openai_non_stream_matches_snapshot(case, fixed_events):
    expected = load_snapshot("openai_non_stream")[case["name"]]
    client = fixed_events(case["events"])
    response = await kiro_gateway._non_stream_runtime(
        client, "access-token", {"conversationState": {}}, "claude-sonnet-5"
    )
    assert fakes.normalize(response) == expected


@pytest.mark.parametrize("case", ANTHROPIC_STREAM_CASES, ids=lambda case: case["name"])
async def test_anthropic_stream_matches_snapshot(case, fixed_events):
    expected = load_snapshot("anthropic_stream")[case["name"]]
    client = fixed_events(case["events"])
    lines = [
        line
        async for line in kiro_gateway._anthropic_stream_runtime(
            client, "access-token", case["state"], "claude-sonnet-5"
        )
    ]
    assert _normalize_sse_lines(lines) == expected
