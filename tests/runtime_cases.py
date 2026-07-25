"""Casos de flujo de eventos del runtime para los snapshots de streaming.

Cada caso es una secuencia de payloads tal como los emite el runtime de Kiro
tras decodificar el AWS EventStream. Se consumen tanto por el generador de
snapshots como por los tests de regresión de las fases 4 y 8.
"""

RUNTIME_CASES = [
    {
        "name": "solo_texto",
        "events": [
            {"content": "Hola"},
            {"content": " mundo"},
            {"usage": {"inputTokens": 12, "outputTokens": 3}},
        ],
    },
    {
        "name": "una_tool_call_fragmentada",
        "events": [
            {"name": "terminal", "toolUseId": "tooluse_one", "input": ""},
            {"name": "terminal", "toolUseId": "tooluse_one", "input": '{"command"'},
            {"name": "terminal", "toolUseId": "tooluse_one", "input": ': "pwd"}'},
            {"stop": True},
        ],
    },
    {
        "name": "tool_use_input_por_evento_separado",
        "events": [
            {"name": "read", "toolUseId": "tooluse_read", "input": ""},
            {"input": '{"path"'},
            {"input": ': "a.txt"}'},
            {"stop": True},
        ],
    },
    {
        "name": "dos_tool_calls",
        "events": [
            {"name": "read", "toolUseId": "tooluse_a", "input": '{"path":"a"}'},
            {"stop": True},
            {"name": "read", "toolUseId": "tooluse_b", "input": '{"path":"b"}'},
            {"stop": True},
        ],
    },
    {
        "name": "texto_y_tool_call",
        "events": [
            {"content": "Voy a ejecutarlo"},
            {"name": "terminal", "toolUseId": "tooluse_mix", "input": '{"command":"ls"}'},
            {"stop": True},
            {"usage": {"inputTokens": 30, "outputTokens": 8}},
        ],
    },
    {
        "name": "evento_de_error",
        "events": [
            {"content": "Empiezo"},
            {"error": "ThrottlingException", "message": "Rate exceeded"},
        ],
    },
    {
        "name": "uso_de_contexto_ignorado",
        "events": [
            {"content": "Texto"},
            {"contextUsagePercentage": 42.5},
            {"usage": {"inputTokens": 5, "outputTokens": 1}},
        ],
    },
    {
        "name": "tool_call_en_formato_bracket",
        "events": [
            {"content": 'Ejecutando [Called terminal with args: {"command": "pwd"}] listo'},
        ],
    },
    {
        "name": "sin_eventos",
        "events": [],
    },
]


ANTHROPIC_STREAM_CASES = [
    {
        "name": "texto_simple",
        "events": [
            {"content": "Hola"},
            {"content": " mundo"},
            {"usage": {"inputTokens": 12, "outputTokens": 3}},
        ],
        "state": {"conversationState": {"currentMessage": {"userInputMessage": {}}}},
    },
    {
        "name": "tool_use",
        "events": [
            {"name": "terminal", "toolUseId": "tooluse_one", "input": '{"command":"pwd"}'},
            {"stop": True},
        ],
        "state": {"conversationState": {"currentMessage": {"userInputMessage": {}}}},
    },
    {
        "name": "texto_y_tool_use",
        "events": [
            {"content": "Ahora lo ejecuto"},
            {"name": "terminal", "toolUseId": "tooluse_two", "input": '{"command":"ls"}'},
            {"stop": True},
        ],
        "state": {"conversationState": {"currentMessage": {"userInputMessage": {}}}},
    },
]
