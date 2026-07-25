"""Genera los snapshots de referencia del comportamiento actual.

Se ejecuta una sola vez contra el monolito original (fase 1) y su salida queda
versionada en `tests/snapshots/`. Las fases 4 y 8 del refactor deben reproducir
esos ficheros byte a byte.

Uso: python -m tests.generate_snapshots
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kiro_gateway  # noqa: E402
from tests import fakes  # noqa: E402
from tests.cases import (  # noqa: E402
    ANTHROPIC_REQUEST_CASES,
    CONVERSATION_CASES,
    MODEL_ID_CASES,
)
from tests.runtime_cases import ANTHROPIC_STREAM_CASES, RUNTIME_CASES  # noqa: E402

SNAPSHOT_DIR = ROOT / "tests" / "snapshots"


def _write(name, payload):
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SNAPSHOT_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n")
    return path


def build_conversation_snapshots():
    results = {}
    for case in CONVERSATION_CASES:
        state = kiro_gateway._build_conversation_state(
            case["model_id"],
            case["messages"],
            system_prompt=case["system_prompt"],
            tools=case["tools"],
            enforce_tool_continuation=case["enforce_tool_continuation"],
        )
        results[case["name"]] = fakes.normalize(state)
    return results


def build_anthropic_request_snapshots():
    results = {}
    for case in ANTHROPIC_REQUEST_CASES:
        request = kiro_gateway.AnthropicRequest(**case["request"])
        messages, tools = kiro_gateway._convert_anthropic_request(request)
        results[case["name"]] = fakes.normalize({"messages": messages, "tools": tools})
    return results


def build_model_id_snapshots():
    return {case: kiro_gateway._normalize_model_id(case) for case in MODEL_ID_CASES}


async def _collect_openai_stream(events):
    reader = fakes.event_reader(events)
    original = kiro_gateway.read_aws_eventstream
    kiro_gateway.read_aws_eventstream = reader
    try:
        client = fakes.FakeRuntimeClient()
        return [
            line
            async for line in kiro_gateway._stream_runtime(
                client, "access-token", {"conversationState": {}}, "claude-sonnet-5"
            )
        ]
    finally:
        kiro_gateway.read_aws_eventstream = original


async def _collect_non_stream(events):
    reader = fakes.event_reader(events)
    original = kiro_gateway.read_aws_eventstream
    kiro_gateway.read_aws_eventstream = reader
    try:
        client = fakes.FakeRuntimeClient()
        return await kiro_gateway._non_stream_runtime(
            client, "access-token", {"conversationState": {}}, "claude-sonnet-5"
        )
    finally:
        kiro_gateway.read_aws_eventstream = original


async def _collect_anthropic_stream(events, state):
    reader = fakes.event_reader(events)
    original = kiro_gateway.read_aws_eventstream
    kiro_gateway.read_aws_eventstream = reader
    try:
        client = fakes.FakeRuntimeClient()
        return [
            line
            async for line in kiro_gateway._anthropic_stream_runtime(
                client, "access-token", state, "claude-sonnet-5"
            )
        ]
    finally:
        kiro_gateway.read_aws_eventstream = original


def _normalize_sse_lines(lines):
    """Normaliza los identificadores generados dentro de las líneas SSE."""
    normalized = []
    for line in lines:
        if line.startswith("data: {"):
            payload = json.loads(line.removeprefix("data: ").strip())
            normalized.append({"data": fakes.normalize(payload)})
        elif line.startswith("event: "):
            event, _, data = line.partition("\ndata: ")
            normalized.append({
                "event": event.removeprefix("event: ").strip(),
                "data": fakes.normalize(json.loads(data.strip())) if data.strip() else None,
            })
        else:
            normalized.append({"raw": line.strip()})
    return normalized


async def build_runtime_snapshots():
    openai_stream = {}
    non_stream = {}
    for case in RUNTIME_CASES:
        openai_stream[case["name"]] = _normalize_sse_lines(
            await _collect_openai_stream(case["events"])
        )
        non_stream[case["name"]] = fakes.normalize(await _collect_non_stream(case["events"]))

    anthropic_stream = {}
    for case in ANTHROPIC_STREAM_CASES:
        anthropic_stream[case["name"]] = _normalize_sse_lines(
            await _collect_anthropic_stream(case["events"], case["state"])
        )

    return openai_stream, non_stream, anthropic_stream


async def main():
    written = [
        _write("conversation_state", build_conversation_snapshots()),
        _write("anthropic_requests", build_anthropic_request_snapshots()),
        _write("model_ids", build_model_id_snapshots()),
    ]
    openai_stream, non_stream, anthropic_stream = await build_runtime_snapshots()
    written.append(_write("openai_stream", openai_stream))
    written.append(_write("openai_non_stream", non_stream))
    written.append(_write("anthropic_stream", anthropic_stream))

    for path in written:
        print(f"escrito {path.relative_to(ROOT)}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
