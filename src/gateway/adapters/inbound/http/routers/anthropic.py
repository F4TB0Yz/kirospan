"""Rutas `POST /v1/messages` y `POST /v1/messages/count_tokens`, formato Anthropic."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .....application.chat import ChatService
from .....domain.errors import InvalidRequest, UpstreamError
from .....domain.model_id import normalize_model_id
from .....protocols.anthropic.requests import anthropic_content_text, convert_anthropic_request
from .....protocols.anthropic.responses import (
    anthropic_response_from_openai,
    estimate_tokens,
)
from .....protocols.anthropic.sse_presenter import anthropic_stream_presenter
from .....protocols.kiro.conversation import build_conversation_state
from .....protocols.kiro.reasoning import (
    build_additional_model_request_fields,
    thinking_system_markers,
)
from .....protocols.openai.non_stream_presenter import openai_non_stream
from .....protocols.openai.sse_presenter import openai_stream
from .....domain.images import extract_kiro_images
from ..schemas import AnthropicRequest
from ._shared import resolve_profile_arn

log = logging.getLogger("kirospan")

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def build_anthropic_router(token_service, model_catalog, runtime_client_factory) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/messages/count_tokens")
    async def anthropic_count_tokens(req: AnthropicRequest):
        messages, _ = convert_anthropic_request(req)
        try:
            text_characters = sum(
                len(anthropic_content_text(message.get("content", "")))
                for message in messages
            )
            image_count = sum(len(extract_kiro_images(message)) for message in messages)
        except InvalidRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"input_tokens": estimate_tokens(text_characters, image_count)}

    @router.post("/v1/messages")
    async def anthropic_messages(req: AnthropicRequest):
        token = await token_service.ensure()
        access_token = token.get("access_token", "")
        profile_arn = await resolve_profile_arn(token, token_service, model_catalog)

        messages, tools = convert_anthropic_request(req)
        backend_model = normalize_model_id(req.model)
        if backend_model != req.model:
            log.info(f"Model alias normalized: {req.model} -> {backend_model}")

        additional_fields = build_additional_model_request_fields(
            backend_model,
            thinking=req.thinking,
            output_config=req.output_config,
        )
        try:
            state = build_conversation_state(
                backend_model,
                messages,
                system_prompt=thinking_system_markers(additional_fields),
                tools=tools or None,
                enforce_tool_continuation=False,
            )
        except InvalidRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        state["profileArn"] = profile_arn
        state["agentMode"] = "vibe"
        if additional_fields:
            state["additionalModelRequestFields"] = additional_fields

        if req.stream:
            async def stream_runtime(client_, access_token_, state_, model_id_):
                chat_service = ChatService(runtime_client_factory())
                events = chat_service.run(access_token=access_token_, state=state_)
                async for line in openai_stream(events, model_id_):
                    yield line

            return StreamingResponse(
                anthropic_stream_presenter(stream_runtime, None, access_token, state, req.model),
                media_type="text/event-stream",
                headers=SSE_HEADERS,
            )

        try:
            chat_service = ChatService(runtime_client_factory())
            events = chat_service.run(access_token=access_token, state=state)
            response = await openai_non_stream(events, req.model)
        except UpstreamError as exc:
            raise HTTPException(exc.status_code or 502, str(exc)) from exc
        return anthropic_response_from_openai(response)

    return router
