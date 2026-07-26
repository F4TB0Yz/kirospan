"""Clasificación de los eventos que emite el runtime de Kiro.

El runtime no rellena de forma fiable la cabecera `:event-type` del
EventStream, así que el tipo se infiere de las claves presentes en el payload.
El orden de las comprobaciones importa: `toolUse` se distingue de
`toolUseInput` por llevar `name`, y `toolUseStop` de `contextUsage` por no
llevar el porcentaje.

Reasoning: Kiro emite dos variantes de `reasoningContent`:
- Claude legible: {"reasoningContent": {"text": "...", "signature": "..."}}
- GPT redactado:  {"reasoningContent": {"redactedContent": "...blob..."}}
Solo la variante con `text` se propaga como thinking; la redactada se descarta.
"""

import json
from typing import Optional


def _extract_reasoning(parsed: dict) -> dict | None:
    """Extrae reasoning envuelto o inferido por su estructura."""
    reasoning = parsed.get("reasoningContent")
    if not isinstance(reasoning, dict):
        reasoning = parsed if any(
            key in parsed for key in ("text", "signature", "redactedContent")
        ) else None
    if not isinstance(reasoning, dict):
        return None

    text = reasoning.get("text")
    signature = reasoning.get("signature")
    if isinstance(text, str) or isinstance(signature, str):
        return {
            "text": text if isinstance(text, str) else "",
            "signature": signature if isinstance(signature, str) else "",
        }
    if "redactedContent" in reasoning:
        return {}
    return None


def _as_json_text(value) -> str:
    """El runtime manda el input de una tool como texto o como objeto."""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value if isinstance(value, str) else str(value or "")


def parse_kiro_event(parsed: dict) -> Optional[dict]:
    """Devuelve `{"type", "data"}` o `None` si el evento no interesa."""
    if not isinstance(parsed, dict):
        return None

    # Reasoning/thinking — check first so it takes precedence
    reasoning = _extract_reasoning(parsed)
    if reasoning is not None:
        return {"type": "thinking", "data": reasoning} if reasoning else None

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
