"""Catálogo de modelos en el formato de `/v1/models` de OpenAI."""

DEFAULT_MAX_INPUT_TOKENS = 200000
DEFAULT_MAX_OUTPUT_TOKENS = 64000

# Se usa solo si el plano de gestión de Kiro no responde. Los identificadores
# coinciden con los valores de destino de MODEL_ALIASES (domain/model_id.py):
# el monolito anunciaba aquí "claude-sonnet-4.6"/"claude-opus-4.8", IDs que el
# runtime de Kiro rechaza con INVALID_MODEL_ID porque no son ninguno de los
# alias reconocidos ni un modelId real.
FALLBACK_MODELS = [
    {"modelId": "auto", "description": "Auto-select"},
    {
        "modelId": "claude-sonnet-5",
        "description": "Claude Sonnet 5",
        "tokenLimits": {"maxInputTokens": 1000000},
    },
    {
        "modelId": "claude-opus-5",
        "description": "Claude Opus 5",
        "tokenLimits": {"maxInputTokens": 1000000},
    },
    {
        "modelId": "claude-haiku-4.5",
        "description": "Claude Haiku 4.5",
        "tokenLimits": {"maxInputTokens": 200000},
    },
]


def to_openai_model_list(kiro_models: list[dict], created: int) -> list[dict]:
    """Traduce el catálogo de Kiro al formato que esperan los clientes OpenAI."""
    return [
        {
            "id": model.get("modelId", ""),
            "object": "model",
            "created": created,
            "owned_by": "kiro",
            "root": model.get("modelId", ""),
            "max_input_tokens": (model.get("tokenLimits") or {}).get(
                "maxInputTokens", DEFAULT_MAX_INPUT_TOKENS
            ),
            "max_output_tokens": (model.get("tokenLimits") or {}).get(
                "maxOutputTokens", DEFAULT_MAX_OUTPUT_TOKENS
            ),
            "description": model.get("description", ""),
            "rate_multiplier": model.get("rateMultiplier", 1.0),
        }
        for model in kiro_models
        if model.get("modelId")
    ]
