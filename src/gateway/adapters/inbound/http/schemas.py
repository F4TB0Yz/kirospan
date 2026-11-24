"""Modelos de petición HTTP: los `pydantic.BaseModel` de la frontera."""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class ChatRequest(BaseModel):
    """Cuerpo de `POST /v1/chat/completions`, formato OpenAI."""

    model: str
    messages: list[dict]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[dict | str] = None
    reasoning_effort: Optional[str] = None


class AnthropicRequest(BaseModel):
    """Cuerpo de `POST /v1/messages`, formato Anthropic."""

    model: str
    messages: list[dict]
    system: Optional[str | list[dict]] = None
    stream: bool = False
    max_tokens: int = 4096
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[dict | str] = None
    thinking: Optional[dict] = None
    output_config: Optional[dict] = None


class ResponsesRequest(BaseModel):
    """Cuerpo de `POST /v1/responses`, formato OpenAI Responses API.

    Codex y los clientes nuevos envían campos extra (`store`,
    `previous_response_id`, `parallel_tool_calls`, `metadata`...) que este
    gateway no necesita: se toleran y se ignoran.
    """

    model: str
    input: str | list[dict]
    instructions: Optional[str] = None
    stream: bool = False
    max_output_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[dict | str] = None
    reasoning: Optional[dict | str] = None
    previous_response_id: Optional[str] = None
    store: Optional[bool] = None
    parallel_tool_calls: Optional[bool] = None
    model_config = ConfigDict(extra="allow")
