"""Conversión de especificaciones de herramientas al formato de Kiro."""

PLACEHOLDER_DESCRIPTION = "Tool"
PLACEHOLDER_SCHEMA = {"type": "object", "properties": {}}

# Combinadores JSON Schema que el runtime de Kiro rechaza en el nivel
# superior de `input_schema` con `TOOL_SCHEMA_INVALID`.
_TOP_LEVEL_COMBINATORS = ("oneOf", "allOf", "anyOf")


def _sanitize_tool_schema(schema: dict) -> dict:
    """Normaliza un JSON Schema para que Kiro lo acepte.

    El runtime rechaza `oneOf`/`allOf`/`anyOf` en el nivel superior del
    `input_schema` (lo envían clientes como Codex). Fusionamos la primera
    rama viable del combinador en el nivel superior y descartamos el
    combinador. Si no hay rama utilizable, caemos a un objeto genérico.
    """
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    present = [k for k in _TOP_LEVEL_COMBINATORS if k in schema]
    if not present:
        return schema

    sanitized = {k: v for k, v in schema.items() if k not in _TOP_LEVEL_COMBINATORS}
    for key in present:
        branches = schema.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    # Fusiona la rama sin pisar claves ya presentes.
                    for bk, bv in branch.items():
                        sanitized.setdefault(bk, bv)
                    break

    sanitized.setdefault("type", "object")
    sanitized.setdefault("properties", {})
    return sanitized


def convert_tools_to_kiro(tools: list[dict]) -> list[dict]:
    """Traduce herramientas OpenAI (`function`) a `toolSpecification` de Kiro."""
    kiro_tools = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        function = tool.get("function", {})
        # Kiro rechaza con HTTP 400 cualquier tool cuyo `description` sea
        # vacío. Clientes como Codex envían herramientas internas sin
        # descripción, así que caemos a un placeholder no vacío.
        description = function.get("description") or PLACEHOLDER_DESCRIPTION
        schema = _sanitize_tool_schema(function.get("parameters", {}))
        kiro_tools.append({
            "toolSpecification": {
                "name": function.get("name", ""),
                "description": description,
                "inputSchema": {"json": schema},
            }
        })
    return kiro_tools


def _tool_names_in_history(history: list[dict]) -> set[str]:
    names = set()
    for entry in history:
        message = entry.get("assistantResponseMessage", {})
        for tool_use in message.get("toolUses", []):
            if tool_use.get("name"):
                names.add(tool_use["name"])
    return names


def add_placeholder_tools(tools: list[dict], history: list[dict]) -> list[dict]:
    """Redeclara las herramientas usadas en turnos anteriores.

    Kiro rechaza un `toolResult` cuyo `toolSpecification` no venga en la
    petición, y los clientes no reenvían las herramientas de turnos pasados.
    """
    history_names = _tool_names_in_history(history)
    if not history_names:
        return tools

    declared = {
        name
        for tool in tools
        if (name := tool.get("toolSpecification", {}).get("name"))
    }
    missing = history_names - declared
    if not missing:
        return tools

    placeholders = [
        {
            "toolSpecification": {
                "name": name,
                "description": PLACEHOLDER_DESCRIPTION,
                "inputSchema": {"json": PLACEHOLDER_SCHEMA},
            }
        }
        for name in missing
    ]
    return tools + placeholders
