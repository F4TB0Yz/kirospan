"""Construcción del control de reasoning nativo del runtime de Kiro."""

import re
from typing import Any


_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_CLAUDE_WITH_SUMMARIZED_THINKING = {
    "claude-opus-5",
    "claude-opus-4.7",
    "claude-opus-4.8",
    "claude-sonnet-5",
}


def _requested_effort(
    reasoning_effort: str | None,
    thinking: dict[str, Any] | None,
    output_config: dict[str, Any] | None,
) -> str | None:
    thinking_type = str((thinking or {}).get("type", "")).lower()
    if thinking_type in {"disabled", "off"}:
        return None

    raw_effort = reasoning_effort or (output_config or {}).get("effort")
    if raw_effort is None and thinking_type in {"adaptive", "enabled"}:
        raw_effort = "medium"
    if raw_effort is None:
        return None

    effort = str(raw_effort).lower()
    if effort in {"none", "off"}:
        return None
    if effort == "minimal":
        return "low"
    return effort if effort in _EFFORTS else None


def build_additional_model_request_fields(
    model_id: str,
    *,
    reasoning_effort: str | None = None,
    thinking: dict[str, Any] | None = None,
    output_config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Traduce controles OpenAI/Anthropic al campo raíz que consume Kiro."""
    effort = _requested_effort(reasoning_effort, thinking, output_config)
    if not effort:
        return None

    normalized_model = model_id.lower()
    base_model = re.sub(r"\.1m$", "", normalized_model)
    if normalized_model.startswith(("openai-gpt", "gpt-")):
        if effort == "max":
            effort = "xhigh"
        return {"reasoning": {"effort": effort}}

    if normalized_model.startswith("claude-"):
        if effort == "xhigh" and base_model in {
            "claude-opus-4.6",
            "claude-sonnet-4.6",
        }:
            effort = "max"
        thinking_config: dict[str, str] = {"type": "adaptive"}
        if base_model in _CLAUDE_WITH_SUMMARIZED_THINKING:
            thinking_config["display"] = "summarized"
        return {
            "output_config": {"effort": effort},
            "thinking": thinking_config,
        }

    return None


def thinking_system_markers(additional_fields: dict[str, Any] | None) -> str:
    """Marcadores que hacen que Kiro conserve el thinking visible de Claude."""
    if not additional_fields or "thinking" not in additional_fields:
        return ""
    effort = additional_fields.get("output_config", {}).get("effort")
    budget = {
        "medium": 20000,
        "high": 30000,
        "xhigh": 50000,
        "max": 50000,
    }.get(effort, 10000)
    return (
        "<thinking_mode>enabled</thinking_mode>"
        f"<max_thinking_length>{budget}</max_thinking_length>"
    )
