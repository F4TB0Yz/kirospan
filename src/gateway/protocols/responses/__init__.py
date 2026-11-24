"""Protocolo OpenAI Responses API (`/v1/responses`), usado por Codex.

Kiro no habla el Responses API: estos módulos normalizan su `input`,
`instructions` y `tools` al formato de mensajes OpenAI que consume
`build_conversation_state`, y presentan los `AssistantEvent` del `ChatService`
como respuestas Responses (JSON único o stream SSE).
"""