"""Casos de entrada representativos para los snapshots de regresión.

Cada caso ejercita una regla distinta del protocolo de conversación de Kiro.
Se mantienen como datos puros (sin dependencias) para que tanto el generador
de snapshots como los tests de regresión consuman exactamente lo mismo.
"""

CONVERSATION_CASES = [
    {
        "name": "solo_usuario",
        "model_id": "claude-sonnet-5",
        "messages": [{"role": "user", "content": "Hola"}],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "system_colapsado_en_primer_usuario",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "system", "content": "Eres un agente."},
            {"role": "user", "content": "Primer turno"},
            {"role": "assistant", "content": "Respuesta"},
            {"role": "user", "content": "Segundo turno"},
        ],
        "system_prompt": "Prompt externo",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "usuarios_consecutivos_fusionados",
        "model_id": "claude-opus-5",
        "messages": [
            {"role": "user", "content": "Uno"},
            {"role": "user", "content": "Dos"},
            {"role": "assistant", "content": "Ok"},
            {"role": "user", "content": "Tres"},
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "tool_call_con_resultado",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": "Lista el directorio"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command":"ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"},
        ],
        "system_prompt": "",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "terminal",
                    "description": "Ejecuta un comando",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                },
            }
        ],
        "enforce_tool_continuation": False,
    },
    {
        "name": "tool_call_con_continuacion_forzada",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": "Lista el directorio"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": '{"command":"ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"},
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": True,
    },
    {
        "name": "tool_results_paralelos",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": "Dos cosas a la vez"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_a",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"a"}'},
                    },
                    {
                        "id": "call_b",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"b"}'},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "call_a", "content": "contenido a"},
            {"role": "tool", "tool_call_id": "call_b", "content": "contenido b"},
            {"role": "user", "content": "Sigue"},
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "tool_result_con_error",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": "Falla esto"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_err",
                        "type": "function",
                        "function": {"name": "terminal", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_err",
                "content": "command not found",
                "is_error": True,
            },
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "historial_con_tool_sin_spec_declarada",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": "Turno uno"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_old",
                        "type": "function",
                        "function": {"name": "herramienta_antigua", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_old", "content": "resultado"},
            {"role": "assistant", "content": "Listo"},
            {"role": "user", "content": "Turno dos"},
        ],
        "system_prompt": "",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "herramienta_nueva",
                    "description": "Otra",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "enforce_tool_continuation": False,
    },
    {
        "name": "imagen_en_mensaje_actual",
        "model_id": "claude-sonnet-5",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe la imagen"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": "iVBORw0KGgo=",
                        },
                    },
                ],
            }
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "bloques_de_contenido_en_lista",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Parte uno"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "Respondo"}]},
            {"role": "user", "content": [{"type": "text", "text": "Parte dos"}]},
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "historial_terminado_en_assistant",
        "model_id": "claude-sonnet-5",
        "messages": [
            {"role": "user", "content": "Pregunta"},
            {"role": "assistant", "content": "Respuesta previa"},
        ],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
    {
        "name": "sin_mensajes",
        "model_id": "auto",
        "messages": [],
        "system_prompt": "",
        "tools": None,
        "enforce_tool_continuation": False,
    },
]


ANTHROPIC_REQUEST_CASES = [
    {
        "name": "texto_simple_con_system",
        "request": {
            "model": "claude-sonnet-5",
            "system": "Eres un agente.",
            "messages": [{"role": "user", "content": "Hola"}],
            "max_tokens": 1024,
        },
    },
    {
        "name": "system_como_lista_de_bloques",
        "request": {
            "model": "claude-sonnet-5",
            "system": [{"type": "text", "text": "Bloque uno"}],
            "messages": [{"role": "user", "content": "Hola"}],
            "max_tokens": 1024,
        },
    },
    {
        "name": "tool_use_y_tool_result",
        "request": {
            "model": "claude-sonnet-5",
            "messages": [
                {"role": "user", "content": "Ejecuta ls"},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "Voy a ejecutarlo"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "terminal",
                            "input": {"command": "ls"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "a.txt",
                        }
                    ],
                },
            ],
            "max_tokens": 1024,
            "tools": [
                {
                    "name": "terminal",
                    "description": "Ejecuta un comando",
                    "input_schema": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                    },
                }
            ],
        },
    },
    {
        "name": "tool_result_fallido",
        "request": {
            "model": "claude-sonnet-5",
            "messages": [
                {"role": "user", "content": "Falla"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_err",
                            "name": "terminal",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_err",
                            "content": "boom",
                            "is_error": True,
                        }
                    ],
                },
            ],
            "max_tokens": 1024,
        },
    },
    {
        "name": "imagen_base64",
        "request": {
            "model": "claude-sonnet-5",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Qué es esto"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": "iVBORw0KGgo=",
                            },
                        },
                    ],
                }
            ],
            "max_tokens": 1024,
        },
    },
]


MODEL_ID_CASES = [
    "claude-fable-5",
    "fable-5",
    "fable",
    "sonnet",
    "opus",
    "haiku",
    "claude-opus-4-8",
    "claude-opus-4-8[1m]",
    "claude-fable-5[1m]",
    "claude-fable-5[22m",
    "\x1b[1mclaude-opus-4-8\x1b[0m",
    "claude-sonnet-5",
    "claude-sonnet-4-5-20250101",
    "auto",
    "gpt-5.6-sol",
    "  claude-sonnet-5  ",
]
