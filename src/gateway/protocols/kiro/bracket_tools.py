"""Tool calls emitidas como texto plano.

Algunos modelos, en lugar de usar el canal `toolUse` del runtime, escriben la
llamada dentro del texto: `[Called nombre with args: {...}]`. El JSON se
delimita contando llaves en lugar de con una expresión regular, porque los
argumentos pueden contener objetos anidados.
"""

import json
import re
import uuid

_CALL_PREFIX_RE = re.compile(r"\[Called\s+([\w-]+)\s+with\s+args:\s*")


def _json_object_end(text: str, start: int) -> int:
    """Índice siguiente al cierre del objeto JSON que empieza en `start`."""
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    return start


def parse_bracket_tool_calls(text: str) -> tuple[list[dict], str]:
    """Extrae las tool calls embebidas y devuelve también el texto sin ellas."""
    matches = list(_CALL_PREFIX_RE.finditer(text))
    if not matches:
        return [], text

    tool_calls: list[dict] = []
    removals: list[tuple[int, int]] = []

    for match in matches:
        json_start = match.end()
        if json_start >= len(text) or text[json_start] != "{":
            continue

        json_end = _json_object_end(text, json_start)
        closing_bracket = text.find("]", json_end)
        if closing_bracket < 0:
            continue
        # Solo se acepta si el corchete cierra justo tras el JSON.
        if text[json_end:closing_bracket].strip():
            continue

        try:
            arguments = json.loads(text[json_start:json_end])
        except json.JSONDecodeError:
            continue

        tool_calls.append({
            "id": f"call_{uuid.uuid4().hex[:24]}",
            "type": "function",
            "function": {
                "name": match.group(1),
                "arguments": json.dumps(arguments),
            },
        })
        removals.append((match.start(), closing_bracket + 1))

    cleaned = text
    for start, end in reversed(removals):
        cleaned = cleaned[:start] + cleaned[end:]

    return tool_calls, cleaned
