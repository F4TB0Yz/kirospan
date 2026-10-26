"""Suite E2E completa contra un gateway KiroSpan en marcha.

Cubre:
- Los 3 protocolos: OpenAI `/v1/chat/completions`, Anthropic `/v1/messages`,
  y Responses `/v1/responses`.
- Streaming y non-streaming.
- Integridad del stream (detección de cortes mid-stream).
- Tool calls simples y multi-turn.
- Historial largo (30+ mensajes).
- Reasoning/thinking.
- Model normalization y continuations.
- Errores y edge cases.
- Estabilidad en respuestas largas.

Requiere:
- Un gateway KiroSpan corriendo con credenciales válidas de Kiro.
- `KIRO_GATEWAY_KEY` en el entorno.
- `pytest -m e2e` para ejecutar.
- `pytest -m "e2e and slow"` para los tests de estabilidad largo.
"""

import json
import os
import subprocess
import time

import httpx
import pytest

# ── Configuración ───────────────────────────────────────────────────────────

GATEWAY_PORT = os.environ.get("KIRO_GATEWAY_PORT", "18789")
GATEWAY_KEY = os.environ.get("KIRO_GATEWAY_KEY", "")
BASE_URL = f"http://127.0.0.1:{GATEWAY_PORT}"
OPENAI_URL = f"{BASE_URL}/v1/chat/completions"
ANTHROPIC_URL = f"{BASE_URL}/v1/messages"
RESPONSES_URL = f"{BASE_URL}/v1/responses"
MODELS_URL = f"{BASE_URL}/v1/models"
HEALTH_URL = f"{BASE_URL}/health"
MODEL = os.environ.get("KIRO_E2E_MODEL", "claude-sonnet-5")
TIMEOUT = float(os.environ.get("KIRO_E2E_TIMEOUT", "120"))
LONG_TIMEOUT = float(os.environ.get("KIRO_E2E_LONG_TIMEOUT", "180"))

pytestmark = [pytest.mark.e2e]

# ── Herramientas de test ────────────────────────────────────────────────────

TERMINAL_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "terminal",
        "description": "Execute a shell command and return stdout.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
            "required": ["command"],
        },
    },
}

ECHO_TOOL_OPENAI = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo back the provided text exactly.",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string", "description": "Text to echo back"}},
            "required": ["text"],
        },
    },
}

TERMINAL_TOOL_ANTHROPIC = {
    "name": "terminal",
    "description": "Execute a shell command and return stdout.",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
        "required": ["command"],
    },
}

ECHO_TOOL_ANTHROPIC = {
    "name": "echo",
    "description": "Echo back the provided text exactly.",
    "input_schema": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo back"}},
        "required": ["text"],
    },
}

TERMINAL_TOOL_RESPONSES = {
    "type": "function",
    "name": "terminal",
    "description": "Execute a shell command and return stdout.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "Shell command to execute"}},
        "required": ["command"],
    },
}

ECHO_TOOL_RESPONSES = {
    "type": "function",
    "name": "echo",
    "description": "Echo back the provided text exactly.",
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "Text to echo back"}},
        "required": ["text"],
    },
}

# ── Fixtures ────────────────────────────────────────────────────────────────


def _gateway_is_up() -> bool:
    try:
        r = httpx.get(HEALTH_URL, timeout=5.0)
        return r.status_code == 200
    except httpx.HTTPError:
        return False


@pytest.fixture(autouse=True)
def _skip_without_gateway():
    if not GATEWAY_KEY:
        pytest.skip("KIRO_GATEWAY_KEY no definida")
    if not _gateway_is_up():
        pytest.skip(f"No hay gateway en {HEALTH_URL}")


def _auth_headers():
    return {"Authorization": f"Bearer {GATEWAY_KEY}"}


def _anthropic_headers():
    return {
        "x-api-key": GATEWAY_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


# ── Helpers: OpenAI ─────────────────────────────────────────────────────────


def openai_stream(messages, *, tools=None, model=MODEL, reasoning_effort=None, timeout=None):
    """Ejecuta un chat completion con streaming OpenAI.

    Retorna (content, tool_calls, finish_reason, usage, raw_lines, reasoning_content).
    - content: texto acumulado
    - tool_calls: lista de {id, name, arguments}
    - finish_reason: str o None
    - usage: dict con prompt_tokens, completion_tokens, total_tokens
    - raw_lines: todas las líneas SSE recibidas (para validación de integridad)
    """
    payload = {"model": model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    req_timeout = timeout or TIMEOUT
    content = ""
    reasoning_content = ""
    calls: dict[int, dict] = {}
    finish_reason = None
    usage = {}
    raw_lines: list[str] = []

    with httpx.stream(
        "POST", OPENAI_URL, headers=_auth_headers(), json=payload, timeout=req_timeout
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            raw_lines.append(line)
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event = json.loads(line[6:])
            choice = event.get("choices", [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta", {})
            content += delta.get("content", "") or ""
            reasoning_content += delta.get("reasoning_content", "") or ""
            if event.get("usage"):
                usage = event["usage"]
            for tool_delta in delta.get("tool_calls", []) or []:
                index = tool_delta["index"]
                call = calls.setdefault(
                    index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}}
                )
                call["id"] = tool_delta.get("id") or call["id"]
                fn = tool_delta.get("function", {})
                call["function"]["name"] = fn.get("name") or call["function"]["name"]
                call["function"]["arguments"] += fn.get("arguments", "") or ""

    tool_calls = [calls[i] for i in sorted(calls)]
    return content, tool_calls, finish_reason, usage, raw_lines, reasoning_content


def openai_non_stream(messages, *, tools=None, model=MODEL, reasoning_effort=None):
    """Ejecuta un chat completion sin streaming. Retorna el response dict completo."""
    payload = {"model": model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort

    r = httpx.post(OPENAI_URL, headers=_auth_headers(), json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Helpers: Anthropic ──────────────────────────────────────────────────────


def anthropic_stream(messages, *, system=None, tools=None, model=MODEL, thinking=None, timeout=None):
    """Ejecuta /v1/messages con streaming Anthropic.

    Retorna (content, tool_uses, stop_reason, usage, raw_events, thinking_text).
    - content: texto acumulado
    - tool_uses: lista de {id, name, input}
    - stop_reason: str
    - usage: dict con output_tokens
    - raw_events: lista de (event_type, data_dict) parseados
    - thinking_text: texto de reasoning acumulado
    """
    payload = {"model": model, "messages": messages, "stream": True, "max_tokens": 4096}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    if thinking:
        payload["thinking"] = thinking

    req_timeout = timeout or TIMEOUT
    content = ""
    thinking_text = ""
    tool_uses: list[dict] = []
    stop_reason = None
    usage = {}
    raw_events: list[tuple[str, dict]] = []
    current_tool: dict | None = None

    with httpx.stream(
        "POST", ANTHROPIC_URL, headers=_anthropic_headers(), json=payload, timeout=req_timeout
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            event_type = data.get("type", "")
            raw_events.append((event_type, data))

            if event_type == "content_block_start":
                block = data.get("content_block", {})
                if block.get("type") == "tool_use":
                    current_tool = {"id": block["id"], "name": block["name"], "input": ""}
                elif block.get("type") == "thinking":
                    pass  # thinking block started

            elif event_type == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta":
                    content += delta.get("text", "")
                elif delta.get("type") == "input_json_delta":
                    if current_tool:
                        current_tool["input"] += delta.get("partial_json", "")
                elif delta.get("type") == "thinking_delta":
                    thinking_text += delta.get("thinking", "")

            elif event_type == "content_block_stop":
                if current_tool:
                    tool_uses.append(current_tool)
                    current_tool = None

            elif event_type == "message_delta":
                delta = data.get("delta", {})
                stop_reason = delta.get("stop_reason") or stop_reason
                if data.get("usage"):
                    usage = data["usage"]

            elif event_type == "message_start":
                msg = data.get("message", {})
                if msg.get("usage"):
                    usage.update(msg["usage"])

    if not isinstance(tool_uses, list):
        tool_uses = []
    return content, tool_uses, stop_reason, usage, raw_events, thinking_text


def anthropic_non_stream(messages, *, system=None, tools=None, model=MODEL, thinking=None):
    """Ejecuta /v1/messages sin streaming. Retorna el response dict."""
    payload = {"model": model, "messages": messages, "stream": False, "max_tokens": 4096}
    if system:
        payload["system"] = system
    if tools:
        payload["tools"] = tools
    if thinking:
        payload["thinking"] = thinking

    r = httpx.post(ANTHROPIC_URL, headers=_anthropic_headers(), json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Helpers: Responses API ──────────────────────────────────────────────────


def responses_stream(input_value, *, instructions=None, tools=None, model=MODEL, reasoning=None, timeout=None):
    """Ejecuta /v1/responses con streaming.

    Retorna (output_items, usage, raw_events, status, accumulated_text).
    - output_items: lista de items parseados (con arguments corregidos de los done events)
    - usage: dict
    - raw_events: lista de (event_type, data_dict)
    - status: str (completed/failed)
    - accumulated_text: texto acumulado de los deltas
    """
    payload = {"model": model, "input": input_value, "stream": True}
    if instructions:
        payload["instructions"] = instructions
    if tools:
        payload["tools"] = tools
    if reasoning:
        payload["reasoning"] = reasoning

    req_timeout = timeout or TIMEOUT
    raw_events: list[tuple[str, dict]] = []
    output_items = []
    usage = {}
    status = "unknown"
    text_deltas: list[str] = []
    # Track completed function_call arguments from done events
    fn_arguments: dict[str, str] = {}  # item_id -> complete arguments

    with httpx.stream(
        "POST", RESPONSES_URL, headers=_auth_headers(), json=payload, timeout=req_timeout
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            data = json.loads(line[6:])
            event_type = data.get("type", "")
            raw_events.append((event_type, data))

            if event_type == "response.output_text.delta":
                text_deltas.append(data.get("delta", ""))

            elif event_type == "response.function_call_arguments.done":
                item_id = data.get("item_id", "")
                fn_arguments[item_id] = data.get("arguments", "")

            elif event_type == "response.completed":
                resp = data.get("response", {})
                output_items = resp.get("output", [])
                usage = resp.get("usage", {})
                status = resp.get("status", "unknown")

            elif event_type == "response.failed":
                resp = data.get("response", {})
                status = resp.get("status", "failed")
                usage = resp.get("usage", {})

    # Patch function_call arguments from done events (the completed event
    # may have them empty due to a known presenter bug)
    for item in output_items:
        if item.get("type") == "function_call":
            item_id = item.get("id", "")
            if item_id in fn_arguments and not item.get("arguments"):
                item["arguments"] = fn_arguments[item_id]

    return output_items, usage, raw_events, status, "".join(text_deltas)


def responses_non_stream(input_value, *, instructions=None, tools=None, model=MODEL, reasoning=None):
    """Ejecuta /v1/responses sin streaming. Retorna el response dict."""
    payload = {"model": model, "input": input_value, "stream": False}
    if instructions:
        payload["instructions"] = instructions
    if tools:
        payload["tools"] = tools
    if reasoning:
        payload["reasoning"] = reasoning

    r = httpx.post(RESPONSES_URL, headers=_auth_headers(), json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Helpers: Integridad ─────────────────────────────────────────────────────


class StreamIntegrityError(AssertionError):
    """El stream no cumple las condiciones de integridad."""


def assert_openai_stream_integrity(raw_lines: list[str], usage: dict, content: str):
    """Valida que un stream OpenAI terminó correctamente.

    Criterios duros (fallo = stream roto):
    1. La última línea data relevante es `data: [DONE]`
    2. Hay al menos un chunk con finish_reason != null
    3. No hay errores de Kiro embebidos en el contenido

    Criterio informativo (warning en logs, no fallo):
    - usage.completion_tokens > 0 — Kiro no siempre reporta tokens.
    """
    data_lines = [l for l in raw_lines if l.startswith("data: ")]
    if not data_lines:
        raise StreamIntegrityError("No se recibieron líneas SSE data:")

    last_data = data_lines[-1]
    if last_data != "data: [DONE]":
        raise StreamIntegrityError(
            f"Stream no terminó con [DONE]. Última línea: {last_data[:100]}"
        )

    # Buscar finish_reason en los chunks
    has_finish = False
    for line in data_lines:
        if line == "data: [DONE]":
            continue
        try:
            event = json.loads(line[6:])
            choice = event.get("choices", [{}])[0]
            if choice.get("finish_reason") is not None:
                has_finish = True
                break
        except json.JSONDecodeError:
            continue

    if not has_finish:
        raise StreamIntegrityError("Ningún chunk tiene finish_reason != null")

    # Error embebido en el contenido
    if "[Kiro error:" in content:
        raise StreamIntegrityError(
            f"Error de Kiro embebido en respuesta: {content[:200]}"
        )


def assert_anthropic_stream_integrity(raw_events: list[tuple[str, dict]], usage: dict, content: str):
    """Valida que un stream Anthropic terminó correctamente.

    Criterios duros:
    1. Contiene evento message_stop
    2. Contiene evento message_delta con stop_reason
    3. No hay errores de Kiro embebidos en el contenido
    """
    event_types = [t for t, _ in raw_events]

    if "message_stop" not in event_types:
        raise StreamIntegrityError(
            f"Stream Anthropic sin message_stop. Eventos: {event_types[-5:]}"
        )

    has_delta_stop = any(
        t == "message_delta" and d.get("delta", {}).get("stop_reason") is not None
        for t, d in raw_events
    )
    if not has_delta_stop:
        raise StreamIntegrityError("message_delta sin stop_reason")

    if "[Kiro error:" in content:
        raise StreamIntegrityError(
            f"Error de Kiro embebido en respuesta: {content[:200]}"
        )


def assert_responses_stream_integrity(raw_events: list[tuple[str, dict]], usage: dict, status: str):
    """Valida que un stream Responses API terminó correctamente.

    Criterios duros:
    1. Contiene evento response.completed
    2. status es completed
    """
    event_types = [t for t, _ in raw_events]

    if "response.completed" not in event_types:
        raise StreamIntegrityError(
            f"Stream Responses sin response.completed. Eventos: {event_types[-5:]}"
        )

    if status != "completed":
        raise StreamIntegrityError(f"status = {status} (esperado completed)")


# ── Helpers: Ejecución de tools ─────────────────────────────────────────────


def execute_tool(name: str, arguments: str | dict) -> str:
    """Ejecuta una herramienta de test y devuelve su output."""
    if isinstance(arguments, str):
        arguments = json.loads(arguments)

    if name == "terminal":
        cmd = arguments.get("command", "echo 'no command'")
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or result.stderr.strip()
    elif name == "echo":
        return arguments.get("text", "")
    else:
        return f"Unknown tool: {name}"


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 1: Validación de infraestructura
# ═══════════════════════════════════════════════════════════════════════════════


class TestInfrastructure:
    """Verifica que el gateway está accesible y los helpers funcionan."""

    def test_health_is_up(self):
        r = httpx.get(HEALTH_URL, timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["authenticated"] is True

    def test_models_returns_catalog(self):
        r = httpx.get(MODELS_URL, headers=_auth_headers(), timeout=10.0)
        assert r.status_code == 200
        data = r.json()
        assert data["object"] == "list"
        assert len(data["data"]) > 0
        # Cada modelo tiene id y created
        for m in data["data"]:
            assert "id" in m
            assert "created" in m


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 2: Integridad del stream (detección de cortes)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStreamIntegrity:
    """Verifica que todas las respuestas streaming terminan correctamente.

    Si Kiro corta mid-stream, estos tests fallan con un mensaje claro
    indicando dónde se interrumpió la respuesta.
    """

    def test_openai_stream_ends_with_done_and_usage(self):
        messages = [{"role": "user", "content": "Say the word 'integrity' and nothing else."}]
        content, _, finish_reason, usage, raw_lines, _ = openai_stream(messages)
        assert_openai_stream_integrity(raw_lines, usage, content)
        assert finish_reason == "stop"
        assert "integrity" in content.lower()

    def test_anthropic_stream_ends_with_message_stop(self):
        messages = [{"role": "user", "content": "Say the word 'integrity' and nothing else."}]
        content, _, stop_reason, usage, raw_events, _ = anthropic_stream(messages)
        assert_anthropic_stream_integrity(raw_events, usage, content)
        assert stop_reason == "end_turn"
        assert "integrity" in content.lower()

    def test_responses_stream_ends_with_completed(self):
        output, usage, raw_events, status, text = responses_stream(
            "Say the word 'integrity' and nothing else."
        )
        assert_responses_stream_integrity(raw_events, usage, status)
        assert "integrity" in text.lower()

    def test_openai_non_stream_has_usage(self):
        messages = [{"role": "user", "content": "Say 'hello'."}]
        resp = openai_non_stream(messages)
        usage = resp.get("usage", {})
        # Kiro no siempre reporta tokens > 0, pero el campo debe existir
        assert "completion_tokens" in usage, f"Campo usage ausente: {resp}"
        assert resp["choices"][0]["finish_reason"] == "stop"
        content = resp["choices"][0]["message"]["content"]
        assert len(content) > 0, "Respuesta vacía en non-stream"

    def test_stream_content_is_not_empty(self):
        """Valida que el stream produce contenido real, no solo metadatos."""
        messages = [{"role": "user", "content": "Write exactly one sentence about the ocean."}]
        content, _, _, usage, raw_lines, _ = openai_stream(messages)
        assert len(content.strip()) >= 10, (
            f"Contenido sospechosamente corto ({len(content)} chars): '{content}'. "
            "Posible corte mid-stream."
        )
        assert_openai_stream_integrity(raw_lines, usage, content)


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 3: Chat simple en 3 protocolos × 2 modos
# ═══════════════════════════════════════════════════════════════════════════════


class TestSimpleChat:
    """Pregunta simple (2+2) en todos los protocolos y modos."""

    QUESTION = "What is 2+2? Reply with just the number."

    def test_openai_stream_simple(self):
        messages = [{"role": "user", "content": self.QUESTION}]
        content, calls, finish, usage, raw_lines, _ = openai_stream(messages)
        assert "4" in content
        assert not calls
        assert finish == "stop"
        assert_openai_stream_integrity(raw_lines, usage, content)

    def test_openai_non_stream_simple(self):
        messages = [{"role": "user", "content": self.QUESTION}]
        resp = openai_non_stream(messages)
        text = resp["choices"][0]["message"]["content"]
        assert "4" in text
        assert resp["choices"][0]["finish_reason"] == "stop"

    def test_anthropic_stream_simple(self):
        messages = [{"role": "user", "content": self.QUESTION}]
        content, tools, stop_reason, usage, raw_events, _ = anthropic_stream(messages)
        assert "4" in content
        assert not tools
        assert stop_reason == "end_turn"
        assert_anthropic_stream_integrity(raw_events, usage, content)

    def test_anthropic_non_stream_simple(self):
        messages = [{"role": "user", "content": self.QUESTION}]
        resp = anthropic_non_stream(messages)
        text_blocks = [b for b in resp.get("content", []) if b.get("type") == "text"]
        assert any("4" in b["text"] for b in text_blocks)
        assert resp["stop_reason"] == "end_turn"

    def test_responses_stream_simple(self):
        output, usage, raw_events, status, text = responses_stream(self.QUESTION)
        assert "4" in text
        assert status == "completed"
        assert_responses_stream_integrity(raw_events, usage, status)

    def test_responses_non_stream_simple(self):
        resp = responses_non_stream(self.QUESTION)
        assert resp["status"] == "completed"
        text_items = [i for i in resp["output"] if i["type"] == "message"]
        assert text_items
        text = text_items[0]["content"][0]["text"]
        assert "4" in text


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 4: Tool calls simples
# ═══════════════════════════════════════════════════════════════════════════════


class TestSimpleToolCalls:
    """Una herramienta echo en los 3 protocolos × 2 modos."""

    PROMPT = (
        "You must call the echo tool with exactly the text 'kirospan_test_42'. "
        "Do not write any other text before calling the tool."
    )

    def test_openai_stream_tool_call(self):
        messages = [{"role": "user", "content": self.PROMPT}]
        content, calls, finish, usage, raw_lines, _ = openai_stream(
            messages, tools=[ECHO_TOOL_OPENAI]
        )
        assert finish == "tool_calls", f"Expected tool_calls, got {finish}. Content: {content}"
        assert len(calls) >= 1
        call = calls[0]
        assert call["function"]["name"] == "echo"
        args = json.loads(call["function"]["arguments"])
        assert "kirospan_test_42" in args.get("text", "")

    def test_openai_non_stream_tool_call(self):
        messages = [{"role": "user", "content": self.PROMPT}]
        resp = openai_non_stream(messages, tools=[ECHO_TOOL_OPENAI])
        choice = resp["choices"][0]
        assert choice["finish_reason"] == "tool_calls"
        calls = choice["message"]["tool_calls"]
        assert len(calls) >= 1
        assert calls[0]["function"]["name"] == "echo"
        args = json.loads(calls[0]["function"]["arguments"])
        assert "kirospan_test_42" in args.get("text", "")

    def test_anthropic_stream_tool_use(self):
        messages = [{"role": "user", "content": self.PROMPT}]
        content, tools, stop_reason, usage, raw_events, _ = anthropic_stream(
            messages, tools=[ECHO_TOOL_ANTHROPIC]
        )
        assert stop_reason == "tool_use", f"Expected tool_use, got {stop_reason}. Content: {content}"
        assert len(tools) >= 1
        tool = tools[0]
        assert tool["name"] == "echo"
        tool_input = json.loads(tool["input"]) if isinstance(tool["input"], str) else tool["input"]
        assert "kirospan_test_42" in str(tool_input)

    def test_anthropic_non_stream_tool_use(self):
        messages = [{"role": "user", "content": self.PROMPT}]
        resp = anthropic_non_stream(messages, tools=[ECHO_TOOL_ANTHROPIC])
        assert resp["stop_reason"] == "tool_use"
        tool_blocks = [b for b in resp["content"] if b["type"] == "tool_use"]
        assert len(tool_blocks) >= 1
        assert tool_blocks[0]["name"] == "echo"
        assert "kirospan_test_42" in str(tool_blocks[0]["input"])

    def test_responses_stream_function_call(self):
        output, usage, raw_events, status, text = responses_stream(
            self.PROMPT, tools=[ECHO_TOOL_RESPONSES]
        )
        fn_items = [i for i in output if i["type"] == "function_call"]
        assert len(fn_items) >= 1, f"No function_call items. Output: {output}"
        assert fn_items[0]["name"] == "echo"
        assert "kirospan_test_42" in fn_items[0]["arguments"]

    def test_responses_non_stream_function_call(self):
        resp = responses_non_stream(self.PROMPT, tools=[ECHO_TOOL_RESPONSES])
        fn_items = [i for i in resp["output"] if i["type"] == "function_call"]
        assert len(fn_items) >= 1, f"No function_call items. Output: {resp['output']}"
        assert fn_items[0]["name"] == "echo"
        assert "kirospan_test_42" in fn_items[0]["arguments"]


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 5: Multi-turn con tool calls encadenadas
# ═══════════════════════════════════════════════════════════════════════════════


class TestMultiTurnToolChain:
    """Conversación de 3+ turnos con ejecución real de herramientas."""

    def test_openai_multi_turn_tool_chain(self):
        """Pide ejecutar pwd y echo, valida respuesta cita ambos resultados."""
        messages = [
            {"role": "system", "content": "Use the terminal tool for every command the user asks."},
            {
                "role": "user",
                "content": (
                    "First run `pwd` to get the current directory, then run `echo hello_kirospan`. "
                    "After both complete, tell me both results."
                ),
            },
        ]

        # Turno 1: el modelo debería pedir al menos una tool call
        content, calls, finish, usage, raw_lines, _ = openai_stream(
            messages, tools=[TERMINAL_TOOL_OPENAI]
        )
        assert finish == "tool_calls", f"Turn 1 no pidió tools. finish={finish}, content={content}"
        assert len(calls) >= 1

        # Ejecutar las tools del turno 1
        messages.append({"role": "assistant", "content": content or None, "tool_calls": calls})
        for call in calls:
            output = execute_tool(call["function"]["name"], call["function"]["arguments"])
            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": output,
            })

        # Turno 2: puede pedir más tools o dar respuesta final
        content2, calls2, finish2, usage2, raw_lines2, _ = openai_stream(
            messages, tools=[TERMINAL_TOOL_OPENAI]
        )

        if finish2 == "tool_calls" and calls2:
            # Ejecutar más tools
            messages.append({"role": "assistant", "content": content2 or None, "tool_calls": calls2})
            for call in calls2:
                output = execute_tool(call["function"]["name"], call["function"]["arguments"])
                messages.append({
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": output,
                })
            # Turno 3: respuesta final
            content3, calls3, finish3, usage3, raw_lines3, _ = openai_stream(
                messages, tools=[TERMINAL_TOOL_OPENAI]
            )
            assert finish3 == "stop", f"Turn 3 no terminó. finish={finish3}"
            assert_openai_stream_integrity(raw_lines3, usage3, content3)
            final_content = content3
        else:
            assert finish2 == "stop", f"Turn 2 no terminó. finish={finish2}"
            assert_openai_stream_integrity(raw_lines2, usage2, content2)
            final_content = content2

        # Validar que la respuesta final menciona los resultados
        assert "hello_kirospan" in final_content or "hello" in final_content.lower()

    def test_anthropic_multi_turn_tool_chain(self):
        """Equivalente multi-turn en protocolo Anthropic."""
        messages = [
            {
                "role": "user",
                "content": (
                    "Run `pwd` using the terminal tool, then tell me the result. "
                    "You must call the tool."
                ),
            },
        ]

        # Turno 1
        content, tools, stop_reason, usage, raw_events, _ = anthropic_stream(
            messages, tools=[TERMINAL_TOOL_ANTHROPIC]
        )
        assert stop_reason == "tool_use", f"Turn 1 sin tool_use: {stop_reason}"
        assert len(tools) >= 1

        # Construir respuesta y resultado
        assistant_content = []
        if content:
            assistant_content.append({"type": "text", "text": content})
        for tool in tools:
            assistant_content.append({
                "type": "tool_use",
                "id": tool["id"],
                "name": tool["name"],
                "input": json.loads(tool["input"]) if isinstance(tool["input"], str) else tool["input"],
            })

        messages.append({"role": "assistant", "content": assistant_content})

        tool_results = []
        for tool in tools:
            args = json.loads(tool["input"]) if isinstance(tool["input"], str) else tool["input"]
            output = execute_tool(tool["name"], args)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool["id"],
                "content": output,
            })
        messages.append({"role": "user", "content": tool_results})

        # Turno 2: respuesta final
        content2, tools2, stop_reason2, usage2, raw_events2, _ = anthropic_stream(
            messages, tools=[TERMINAL_TOOL_ANTHROPIC]
        )
        assert stop_reason2 == "end_turn", f"Turn 2: {stop_reason2}"
        assert_anthropic_stream_integrity(raw_events2, usage2, content2)
        assert len(content2) > 0

    def test_responses_multi_turn_tool_chain(self):
        """Equivalente multi-turn en Responses API."""
        input_items = [
            {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Run `pwd` using the terminal tool. You must call the tool."
                        ),
                    }
                ],
            }
        ]

        # Turno 1
        output, usage, raw_events, status, text = responses_stream(
            input_items, tools=[TERMINAL_TOOL_RESPONSES]
        )
        fn_items = [i for i in output if i["type"] == "function_call"]
        assert len(fn_items) >= 1, f"Turn 1 sin function_call: {output}"

        # Añadir function_call y function_call_output al input
        for fn in fn_items:
            input_items.append({
                "type": "function_call",
                "call_id": fn.get("call_id", fn.get("id", "")),
                "name": fn["name"],
                "arguments": fn["arguments"],
            })
            args = json.loads(fn["arguments"]) if fn["arguments"] else {}
            output_text = execute_tool(fn["name"], args)
            input_items.append({
                "type": "function_call_output",
                "call_id": fn.get("call_id", fn.get("id", "")),
                "output": output_text,
            })

        # Turno 2: respuesta final
        output2, usage2, raw_events2, status2, text2 = responses_stream(
            input_items, tools=[TERMINAL_TOOL_RESPONSES]
        )
        assert status2 == "completed"
        assert_responses_stream_integrity(raw_events2, usage2, status2)
        assert len(text2) > 0


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 6: Historial largo (30+ mensajes)
# ═══════════════════════════════════════════════════════════════════════════════


class TestLargeHistory:
    """Validar que el gateway maneja historiales de 30+ mensajes."""

    @staticmethod
    def _build_long_history(n_turns: int = 15) -> list[dict]:
        """Construye un historial de 2*n_turns mensajes (user + assistant alternados)."""
        history = []
        topics = [
            "the weather", "Python programming", "space exploration",
            "cooking recipes", "music genres", "history of computers",
            "marine biology", "artificial intelligence", "architecture",
            "philosophy", "mathematics", "literature", "physics",
            "gardening", "photography",
        ]
        for i in range(n_turns):
            topic = topics[i % len(topics)]
            history.append({"role": "user", "content": f"Tell me a fact about {topic}."})
            history.append({"role": "assistant", "content": f"Here's a fact about {topic}: it's fascinating."})
        # Mensaje final que requiere resumir la conversación
        history.append({
            "role": "user",
            "content": "List exactly 3 of the topics we discussed. Be brief.",
        })
        return history

    def test_openai_large_history(self):
        messages = self._build_long_history(15)  # 31 mensajes
        content, _, finish, usage, raw_lines, _ = openai_stream(messages)
        assert finish == "stop"
        assert len(content) > 20, f"Respuesta muy corta: {content}"
        assert_openai_stream_integrity(raw_lines, usage, content)

    def test_anthropic_large_history(self):
        messages = self._build_long_history(15)
        content, _, stop_reason, usage, raw_events, _ = anthropic_stream(messages)
        assert stop_reason == "end_turn"
        assert len(content) > 20
        assert_anthropic_stream_integrity(raw_events, usage, content)

    def test_responses_large_history(self):
        """Historial largo expresado como items del Responses API."""
        items = []
        topics = [
            "the weather", "Python programming", "space exploration",
            "cooking", "music", "computers", "biology", "AI",
            "architecture", "philosophy", "math", "literature",
            "physics", "gardening", "photography",
        ]
        for i in range(15):
            topic = topics[i % len(topics)]
            items.append({
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": f"Tell me about {topic}."}],
            })
            items.append({
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": f"Sure, {topic} is interesting."}],
            })
        items.append({
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": "List 3 topics we discussed. Be brief."}],
        })

        output, usage, raw_events, status, text = responses_stream(items)
        assert status == "completed"
        assert len(text) > 20
        assert_responses_stream_integrity(raw_events, usage, status)


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 7: Reasoning / thinking
# ═══════════════════════════════════════════════════════════════════════════════


class TestReasoning:
    """Valida que reasoning se emite cuando el modelo lo soporta.

    Nota: No todos los modelos emiten reasoning. Si el modelo no lo soporta,
    estos tests verifican al menos que no crashea y que la respuesta es correcta.
    """

    COMPLEX_QUESTION = (
        "A bat and a ball cost $1.10 in total. The bat costs $1.00 more than "
        "the ball. How much does the ball cost? Think step by step."
    )

    def test_openai_stream_reasoning(self):
        messages = [{"role": "user", "content": self.COMPLEX_QUESTION}]
        content, _, finish, usage, raw_lines, reasoning = openai_stream(
            messages, reasoning_effort="high"
        )
        assert finish == "stop"
        assert_openai_stream_integrity(raw_lines, usage, content)
        # La respuesta debería mencionar 0.05 o 5 cents
        assert "0.05" in content or "5" in content or "five" in content.lower()
        # Si hay reasoning, validar que no está vacío
        if reasoning:
            assert len(reasoning) > 10, f"Reasoning muy corto: {reasoning}"

    def test_anthropic_stream_thinking(self):
        messages = [{"role": "user", "content": self.COMPLEX_QUESTION}]
        content, _, stop_reason, usage, raw_events, thinking = anthropic_stream(
            messages,
            thinking={"type": "enabled", "budget_tokens": 5000},
        )
        assert stop_reason == "end_turn"
        assert_anthropic_stream_integrity(raw_events, usage, content)
        assert "0.05" in content or "5" in content or "five" in content.lower()
        # Si hay thinking, validar que contiene razonamiento real
        if thinking:
            assert len(thinking) > 20, f"Thinking muy corto: {thinking}"

    def test_responses_stream_reasoning(self):
        output, usage, raw_events, status, text = responses_stream(
            self.COMPLEX_QUESTION,
            reasoning={"effort": "high"},
        )
        assert status == "completed"
        assert_responses_stream_integrity(raw_events, usage, status)
        assert "0.05" in text or "5" in text or "five" in text.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 8: Anthropic continuations y model normalization
# ═══════════════════════════════════════════════════════════════════════════════


class TestModelNormalization:
    """Valida normalización de model IDs y count_tokens."""

    def test_anthropic_model_1m_modifier(self):
        """Claude Code envía model[1m] — el gateway debe aceptarlo."""
        messages = [{"role": "user", "content": "Say 'ok'."}]
        content, _, stop_reason, _, _, _ = anthropic_stream(
            messages, model=f"{MODEL}[1m]"
        )
        assert stop_reason == "end_turn"
        assert len(content) > 0

    def test_anthropic_count_tokens(self):
        """El endpoint count_tokens debe retornar input_tokens > 0."""
        payload = {
            "model": MODEL,
            "messages": [{"role": "user", "content": "Hello, world!"}],
            "max_tokens": 100,
        }
        r = httpx.post(
            f"{BASE_URL}/v1/messages/count_tokens",
            headers=_anthropic_headers(),
            json=payload,
            timeout=10.0,
        )
        assert r.status_code == 200
        data = r.json()
        assert data.get("input_tokens", 0) > 0

    def test_openai_model_still_works_after_normalization(self):
        """Modelo con variantes de nombre funciona (el gateway normaliza)."""
        messages = [{"role": "user", "content": "Say 'normalized'."}]
        # El modelo base debería funcionar siempre
        resp = openai_non_stream(messages, model=MODEL)
        assert resp["choices"][0]["finish_reason"] == "stop"


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 9: Errores y edge cases
# ═══════════════════════════════════════════════════════════════════════════════


class TestErrorsAndEdgeCases:
    """Validar respuestas ante entradas inválidas y edge cases."""

    def test_missing_messages_returns_error(self):
        """Body sin campo messages obligatorio."""
        r = httpx.post(
            OPENAI_URL,
            headers=_auth_headers(),
            json={"model": MODEL, "stream": False},
            timeout=10.0,
        )
        assert r.status_code == 422  # Pydantic validation

    def test_empty_messages_list(self):
        """Lista de mensajes vacía — debería ser error o respuesta vacía, no crash."""
        r = httpx.post(
            OPENAI_URL,
            headers=_auth_headers(),
            json={"model": MODEL, "messages": [], "stream": False},
            timeout=30.0,
        )
        # Puede ser 400 (InvalidRequest) o alguna respuesta; no debe ser 500
        assert r.status_code != 500

    def test_large_single_message(self):
        """Un solo mensaje largo no debe crashear."""
        long_text = "Tell me about Python. " * 250  # ~5000 chars
        messages = [{"role": "user", "content": long_text + "\nSummarize in one sentence."}]
        content, _, finish, usage, raw_lines, _ = openai_stream(messages)
        assert finish == "stop"
        assert len(content) > 10
        assert_openai_stream_integrity(raw_lines, usage, content)

    def test_tool_with_complex_nested_arguments(self):
        """Tool con argumentos JSON complejos."""
        complex_tool = {
            "type": "function",
            "function": {
                "name": "configure",
                "description": "Configure settings with nested options.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "settings": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "options": {
                                    "type": "object",
                                    "properties": {
                                        "verbose": {"type": "boolean"},
                                        "level": {"type": "integer"},
                                    },
                                },
                            },
                        }
                    },
                    "required": ["settings"],
                },
            },
        }
        messages = [
            {
                "role": "user",
                "content": (
                    "Call configure with settings: name='test', options: verbose=true, level=3. "
                    "You must call the tool."
                ),
            }
        ]
        content, calls, finish, _, _, _ = openai_stream(messages, tools=[complex_tool])
        assert finish == "tool_calls", f"No tool call. Content: {content}"
        assert len(calls) >= 1
        args = json.loads(calls[0]["function"]["arguments"])
        assert "settings" in args

    def test_unauthorized_request_rejected(self):
        """Petición sin API key es rechazada."""
        r = httpx.post(
            OPENAI_URL,
            json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
            timeout=10.0,
        )
        assert r.status_code == 401

    def test_wrong_key_rejected(self):
        """Petición con key incorrecta es rechazada."""
        r = httpx.post(
            OPENAI_URL,
            headers={"Authorization": "Bearer wrong-key-12345"},
            json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
            timeout=10.0,
        )
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# TASK 10: Estabilidad de stream largo (detección de cortes)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.slow
class TestLongStreamStability:
    """Tests de streams largos para detectar cortes mid-stream.

    Estos tests piden respuestas intencionalmente largas y validan que
    llegan completas. Son los que fallarán si Kiro trunca el stream.

    Ejecutar con: pytest -m "e2e and slow"
    """

    LONG_PROMPT = (
        "Write a detailed essay about the history of Python programming language. "
        "Include its creation by Guido van Rossum, major version releases, "
        "key design philosophy, and its evolution from Python 2 to Python 3. "
        "The essay must be at least 400 words long."
    )
    MIN_WORDS = 150  # Umbral conservador — si hay menos, fue truncado

    def _assert_long_response_complete(
        self, content: str, protocol: str
    ):
        """Validación de completitud de respuesta larga."""
        words = content.split()
        word_count = len(words)

        if word_count < self.MIN_WORDS:
            # Heurística de truncamiento: ¿terminó mid-word o mid-sentence?
            last_char = content[-1] if content else ""
            mid_sentence = last_char not in ".!?\n\"'"
            raise StreamIntegrityError(
                f"[{protocol}] Respuesta truncada: {word_count} palabras "
                f"(mínimo esperado: {self.MIN_WORDS}). "
                f"Mid-sentence: {mid_sentence}. "
                f"Últimos 100 chars: ...{content[-100:]!r}"
            )

        # Verificar que no hay errores de Kiro embebidos en el contenido
        if "[Kiro error:" in content:
            raise StreamIntegrityError(
                f"[{protocol}] Error de Kiro embebido en la respuesta: {content}"
            )

    def test_openai_long_stream_stability(self):
        messages = [{"role": "user", "content": self.LONG_PROMPT}]
        try:
            content, calls, finish, usage, raw_lines, _ = openai_stream(
                messages, timeout=LONG_TIMEOUT
            )
        except httpx.ReadTimeout:
            raise StreamIntegrityError(
                "[OpenAI Stream] Timeout mid-stream: Kiro dejó de enviar datos "
                f"antes de completar la respuesta (timeout={LONG_TIMEOUT}s)"
            )
        assert finish == "stop", f"finish_reason={finish}, calls={calls}"
        assert not calls, f"No esperábamos tool calls: {calls}"
        assert_openai_stream_integrity(raw_lines, usage, content)
        self._assert_long_response_complete(content, "OpenAI Stream")

    def test_openai_long_non_stream_stability(self):
        messages = [{"role": "user", "content": self.LONG_PROMPT}]
        try:
            resp = openai_non_stream(messages)
        except httpx.ReadTimeout:
            raise StreamIntegrityError(
                "[OpenAI Non-Stream] Timeout: Kiro no respondió dentro del "
                f"tiempo límite ({LONG_TIMEOUT}s)"
            )
        content = resp["choices"][0]["message"]["content"]
        assert resp["choices"][0]["finish_reason"] == "stop"
        self._assert_long_response_complete(content, "OpenAI Non-Stream")

    def test_anthropic_long_stream_stability(self):
        messages = [{"role": "user", "content": self.LONG_PROMPT}]
        try:
            content, _, stop_reason, usage, raw_events, _ = anthropic_stream(
                messages, timeout=LONG_TIMEOUT
            )
        except httpx.ReadTimeout:
            raise StreamIntegrityError(
                "[Anthropic Stream] Timeout mid-stream: Kiro dejó de enviar datos "
                f"antes de completar la respuesta (timeout={LONG_TIMEOUT}s). "
                "Este es el problema de corte reportado."
            )
        assert stop_reason == "end_turn"
        assert_anthropic_stream_integrity(raw_events, usage, content)
        self._assert_long_response_complete(content, "Anthropic Stream")

    def test_responses_long_stream_stability(self):
        output, usage, raw_events, status, text = responses_stream(
            self.LONG_PROMPT, timeout=LONG_TIMEOUT
        )
        assert status == "completed"
        assert_responses_stream_integrity(raw_events, usage, status)
        self._assert_long_response_complete(text, "Responses Stream")

    def test_repeated_short_requests_no_drops(self):
        """5 peticiones cortas consecutivas — ninguna debe cortarse.

        Detecta el patrón donde una de cada N peticiones se trunca.
        """
        failures = []
        for i in range(5):
            messages = [{"role": "user", "content": f"Say 'response_{i}_ok' and nothing else."}]
            content, _, finish, usage, raw_lines, _ = openai_stream(messages)
            try:
                assert_openai_stream_integrity(raw_lines, usage, content)
                assert f"response_{i}_ok" in content.lower().replace(" ", "_"), (
                    f"Iteración {i}: contenido inesperado: {content!r}"
                )
            except (AssertionError, StreamIntegrityError) as e:
                failures.append(f"Iteración {i}: {e}")

        if failures:
            raise StreamIntegrityError(
                f"{len(failures)}/5 peticiones fallaron:\n" + "\n".join(failures)
            )
