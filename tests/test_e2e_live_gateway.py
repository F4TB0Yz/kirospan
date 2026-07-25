"""E2E real contra un gateway en marcha: tool call → ejecución real → respuesta final.

Requiere un proceso `kiro_gateway.py` corriendo con credenciales válidas de
Kiro y acceso a un modelo real; por eso se marca `e2e` y no se ejecuta en la
suite por defecto (`pytest -m e2e` para correrlo a propósito). Si no hay
gateway escuchando en el puerto configurado, se salta automáticamente en vez
de fallar: no es una regresión del código, es la ausencia de un entorno.

Consolida los ~10 scripts sueltos de `legacy/` que hacían esta misma
verificación contra un gateway real (`test_gateway_http_e2e.py`,
`test_tool_e2e.py`, `test_full_tool_flow.py`, `test_hermes_full.py`, etc.):
todos dependen de que un LLM real decida emitir una tool call, así que no son
deterministas ni mockeables sin perder el propósito de la prueba. Este fichero
es el único que sobrevive, con los asserts más estrictos del lote (ejecuta
`pwd` de verdad y compara el resultado exacto, en vez de solo comprobar que la
respuesta "mencione" algo).
"""

import json
import os
import subprocess

import httpx
import pytest

GATEWAY_PORT = os.environ.get("KIRO_GATEWAY_PORT", "18789")
GATEWAY_KEY = os.environ.get("KIRO_GATEWAY_KEY", "")
BASE_URL = f"http://127.0.0.1:{GATEWAY_PORT}/v1"
HEALTH_URL = f"http://127.0.0.1:{GATEWAY_PORT}/health"
MODEL = os.environ.get("KIRO_E2E_MODEL", "claude-sonnet-5")
EXPECTED_CWD = os.getcwd()

TERMINAL_TOOL = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Execute a shell command in the current working directory.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}

pytestmark = pytest.mark.e2e


def _gateway_is_up() -> bool:
    try:
        response = httpx.get(HEALTH_URL, timeout=3.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_without_gateway():
    if not GATEWAY_KEY:
        pytest.skip("KIRO_GATEWAY_KEY no está definida para la prueba live")
    if not _gateway_is_up():
        pytest.skip(f"No hay un gateway real escuchando en {HEALTH_URL}")


def _stream_completion(messages):
    payload = {"model": MODEL, "messages": messages, "tools": [TERMINAL_TOOL], "stream": True}
    content = ""
    calls = {}
    finish_reason = None
    with httpx.stream(
        "POST",
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {GATEWAY_KEY}"},
        json=payload,
        timeout=120.0,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            choice = event.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta", {})
            content += delta.get("content", "") or ""
            for tool_delta in delta.get("tool_calls", []) or []:
                index = tool_delta["index"]
                call = calls.setdefault(
                    index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                call["id"] = tool_delta.get("id", call["id"])
                function = tool_delta.get("function", {})
                call["function"]["name"] = function.get("name", call["function"]["name"])
                call["function"]["arguments"] += function.get("arguments", "") or ""
    return content, [calls[index] for index in sorted(calls)], finish_reason


def test_forced_tool_call_round_trip_matches_the_real_filesystem():
    """La tool call real ejecuta `pwd` y la respuesta final debe citar ese cwd exacto."""
    messages = [
        {"role": "system", "content": "Use tools when the user explicitly requires one."},
        {
            "role": "user",
            "content": (
                "You must call the terminal tool with exactly the command pwd. "
                "After receiving its result, answer in Spanish with the exact directory path."
            ),
        },
    ]

    preface, calls, first_finish = _stream_completion(messages)
    assert first_finish == "tool_calls", (first_finish, preface, calls)
    assert len(calls) == 1, calls
    call = calls[0]
    assert call["function"]["name"] == "terminal", call
    arguments = json.loads(call["function"]["arguments"])
    assert arguments.get("command", "").strip() == "pwd", arguments

    execution = subprocess.run(["pwd"], text=True, capture_output=True, check=True)
    tool_output = execution.stdout

    messages.extend([
        {"role": "assistant", "content": preface or None, "tool_calls": calls},
        {"role": "tool", "name": "terminal", "tool_call_id": call["id"], "content": tool_output},
    ])

    answer, followup_calls, second_finish = _stream_completion(messages)
    assert not followup_calls, followup_calls
    assert second_finish == "stop", (second_finish, answer)
    assert EXPECTED_CWD in answer, answer


def test_health_reports_authenticated():
    response = httpx.get(HEALTH_URL, timeout=5.0)
    assert response.status_code == 200
    assert response.json()["authenticated"] is True


def test_models_endpoint_returns_a_non_empty_catalog():
    response = httpx.get(
        f"{BASE_URL}/models", headers={"x-api-key": GATEWAY_KEY}, timeout=10.0
    )
    assert response.status_code == 200
    assert response.json()["data"]
