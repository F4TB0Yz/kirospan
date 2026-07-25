"""Normalización de identificadores de modelo.

Los clientes envían identificadores en formatos heterogéneos: alias cortos,
sufijos de fecha, la variante con guiones en lugar de puntos, y —en el caso de
Claude Code— secuencias ANSI y el modificador `[1m]` que pide contexto de un
millón de tokens. Kiro solo acepta el identificador base.
"""

import re

MODEL_ALIASES = {
    "claude-fable-5": "gpt-5.6-sol",
    "fable-5": "gpt-5.6-sol",
    "fable": "gpt-5.6-sol",
    "sonnet": "claude-sonnet-5",
    "opus": "claude-opus-5",
    "haiku": "claude-haiku-4.5",
}

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CLAUDE_CONTEXT_MODIFIER_RE = re.compile(r"(?:\[1m\])+$", re.IGNORECASE)
_DANGLING_SGR_FRAGMENT_RE = re.compile(r"(?:\[[0-9;]*m)+$", re.IGNORECASE)
_EDGE_CONTROL_RE = re.compile(r"^[\x00-\x20\x7f]+|[\x00-\x20\x7f]+$")
_DATE_SUFFIX_RE = re.compile(r"-20\d{6}$")
_VERSION_SEPARATOR_RE = re.compile(r"(?<=\d)-(?=\d)")


def sanitize_model_id(model_id: str) -> str:
    """Quita ruido de terminal y el modificador de contexto de Claude Code."""
    cleaned = _ANSI_ESCAPE_RE.sub("", str(model_id or ""))
    cleaned = _EDGE_CONTROL_RE.sub("", cleaned.strip())
    # Claude Code añade [1m] para pedir el contexto de un millón de tokens.
    # Kiro selecciona esa capacidad por su cuenta, así que solo se reenvía la base.
    cleaned = _CLAUDE_CONTEXT_MODIFIER_RE.sub("", cleaned)
    # También se tolera una secuencia SGR incompleta copiada de la terminal.
    cleaned = _DANGLING_SGR_FRAGMENT_RE.sub("", cleaned)
    return _EDGE_CONTROL_RE.sub("", cleaned.strip())


def normalize_model_id(model_id: str, aliases: dict[str, str] | None = None) -> str:
    """Devuelve el identificador que entiende el runtime de Kiro."""
    table = MODEL_ALIASES if aliases is None else aliases
    normalized = sanitize_model_id(model_id).lower()
    normalized = _DATE_SUFFIX_RE.sub("", normalized)
    normalized = table.get(normalized, normalized)
    normalized = _VERSION_SEPARATOR_RE.sub(".", normalized)
    return table.get(normalized, normalized)
