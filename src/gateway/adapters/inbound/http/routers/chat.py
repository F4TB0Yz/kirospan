"""Ruta `POST /v1/chat/completions`, formato OpenAI."""

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from .....application.chat import ChatService
from .....domain.errors import InvalidRequest, UpstreamError
from .....domain.model_id import normalize_model_id
from .....protocols.kiro.conversation import build_conversation_state
from .....protocols.kiro.reasoning import (
    build_additional_model_request_fields,
    thinking_system_markers,
)
from .....protocols.openai.non_stream_presenter import openai_non_stream
from .....protocols.openai.sse_presenter import openai_stream
from ..schemas import ChatRequest
from ..sse_keepalive import sse_keep_alive
from ._shared import resolve_profile_arn

log = logging.getLogger("kirospan")

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def build_chat_router(token_service, model_catalog, runtime_client_factory) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/chat/completions")
    async def chat_completions(req: ChatRequest):
        token = await token_service.ensure()
        access_token = token.get("access_token", "")
        profile_arn = await resolve_profile_arn(token, token_service, model_catalog)

        backend_model = normalize_model_id(req.model)
        if backend_model != req.model:
            log.info(f"Model alias normalized: {req.model} -> {backend_model}")

        additional_fields = build_additional_model_request_fields(
            backend_model, reasoning_effort=req.reasoning_effort
        )
        try:
            state = build_conversation_state(
                backend_model,
                req.messages,
                system_prompt=thinking_system_markers(additional_fields),
                tools=req.tools,
            )
        except InvalidRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        state["profileArn"] = profile_arn
        state["agentMode"] = "vibe"
        if additional_fields:
            state["additionalModelRequestFields"] = additional_fields

        chat_service = ChatService(runtime_client_factory())

        if req.stream:
            events = chat_service.run(access_token=access_token, state=state)
            return StreamingResponse(
                sse_keep_alive(openai_stream(events, req.model)),
                media_type="text/event-stream",
                headers=SSE_HEADERS,
            )

        try:
            events = chat_service.run(
                access_token=access_token,
                state=state,
                propagate_upstream_errors=True,
            )
            return await openai_non_stream(events, req.model)
        except UpstreamError as exc:
            raise HTTPException(exc.status_code or 502, str(exc)) from exc

    return router
