"""Conversión de especificaciones de herramientas al formato de Kiro."""

PLACEHOLDER_DESCRIPTION = "Tool"
PLACEHOLDER_SCHEMA = {"type": "object", "properties": {}}


def convert_tools_to_kiro(tools: list[dict]) -> list[dict]:
    """Traduce herramientas OpenAI (`function`) a `toolSpecification` de Kiro."""
    kiro_tools = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        function = tool.get("function", {})
        kiro_tools.append({
            "toolSpecification": {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "inputSchema": {"json": function.get("parameters", {})},
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
