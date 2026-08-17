"""Ruta `POST /v1/responses`, formato OpenAI Responses API (Codex)."""

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
from .....protocols.responses.input import (
    responses_input_to_messages,
    responses_reasoning_effort,
    responses_tools_to_chat,
)
from .....protocols.responses.presenter import (
    responses_non_stream,
    responses_stream,
)
from ..schemas import ResponsesRequest
from ._shared import resolve_profile_arn

log = logging.getLogger("kirospan")

SSE_HEADERS = {"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}


def build_responses_router(token_service, model_catalog, runtime_client_factory) -> APIRouter:
    router = APIRouter()

    @router.post("/v1/responses")
    async def responses(req: ResponsesRequest):
        token = await token_service.ensure()
        access_token = token.get("access_token", "")
        profile_arn = await resolve_profile_arn(token, token_service, model_catalog)

        try:
            messages = responses_input_to_messages(req.input, req.instructions)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        tools = responses_tools_to_chat(req.tools)
        reasoning_effort = responses_reasoning_effort(req.reasoning)

        backend_model = normalize_model_id(req.model)
        if backend_model != req.model:
            log.info(f"Model alias normalized: {req.model} -> {backend_model}")

        additional_fields = build_additional_model_request_fields(
            backend_model, reasoning_effort=reasoning_effort
        )
        try:
            state = build_conversation_state(
                backend_model,
                messages,
                system_prompt=thinking_system_markers(additional_fields),
                tools=tools,
            )
        except InvalidRequest as exc:
            raise HTTPException(400, str(exc)) from exc
        state["profileArn"] = profile_arn
        state["agentMode"] = "vibe"
        if additional_fields:
            state["additionalModelRequestFields"] = additional_fields

        if req.stream:
            chat_service = ChatService(runtime_client_factory())
            events = chat_service.run(access_token=access_token, state=state)
            return StreamingResponse(
                responses_stream(events, req.model),
                media_type="text/event-stream",
                headers=SSE_HEADERS,
            )

        try:
            chat_service = ChatService(runtime_client_factory())
            events = chat_service.run(
                access_token=access_token,
                state=state,
                propagate_upstream_errors=True,
            )
            return await responses_non_stream(events, req.model)
        except UpstreamError as exc:
            raise HTTPException(exc.status_code or 502, str(exc)) from exc

    return router