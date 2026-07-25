"""Modelos de petición HTTP: los `pydantic.BaseModel` de la frontera."""

from typing import Optional

from pydantic import BaseModel


class ChatRequest(BaseModel):
    """Cuerpo de `POST /v1/chat/completions`, formato OpenAI."""

    model: str
    messages: list[dict]
    stream: bool = False
    max_tokens: Optional[int] = None
    temperature: Optional[float] = None
    tools: Optional[list[dict]] = None
    tool_choice: Optional[dict | str] = None


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
