"""Clasificación de los eventos que emite el runtime de Kiro.

El runtime no rellena de forma fiable la cabecera `:event-type` del
EventStream, así que el tipo se infiere de las claves presentes en el payload.
El orden de las comprobaciones importa: `toolUse` se distingue de
`toolUseInput` por llevar `name`, y `toolUseStop` de `contextUsage` por no
llevar el porcentaje.
"""

import json
from typing import Optional


def _as_json_text(value) -> str:
    """El runtime manda el input de una tool como texto o como objeto."""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value if isinstance(value, str) else str(value or "")


def parse_kiro_event(parsed: dict) -> Optional[dict]:
    """Devuelve `{"type", "data"}` o `None` si el evento no interesa."""
    if not isinstance(parsed, dict):
        return None

    if "content" in parsed:
        return {"type": "content", "data": parsed["content"]}

    if "name" in parsed and "toolUseId" in parsed:
        return {
            "type": "toolUse",
            "data": {
                "name": parsed["name"],
                "toolUseId": parsed["toolUseId"],
                "input": _as_json_text(parsed.get("input", "")),
                "stop": parsed.get("stop"),
            },
        }

    if "input" in parsed and "name" not in parsed:
        return {"type": "toolUseInput", "data": {"input": _as_json_text(parsed["input"])}}

    if "stop" in parsed and "contextUsagePercentage" not in parsed:
        return {"type": "toolUseStop", "data": {"stop": parsed["stop"]}}

    if "contextUsagePercentage" in parsed:
        return {
            "type": "contextUsage",
            "data": {"contextUsagePercentage": parsed["contextUsagePercentage"]},
        }

    if "error" in parsed or "Error" in parsed:
        return {
            "type": "error",
            "data": {
                "error": parsed.get("error") or parsed.get("Error", "unknown"),
                "message": parsed.get("message") or parsed.get("Message") or parsed.get("reason"),
            },
        }

    if "usage" in parsed:
        usage = parsed["usage"]
        if isinstance(usage, dict):
            return {
                "type": "usage",
                "data": {
                    "inputTokens": usage.get("inputTokens"),
                    "outputTokens": usage.get("outputTokens"),
                },
            }

    return None
