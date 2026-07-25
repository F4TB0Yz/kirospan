"""Formato de los frames SSE del protocolo Anthropic."""

import json


def anthropic_sse(event: str, data: dict) -> str:
    """Serializa un evento con su nombre y su payload, como espera Anthropic."""
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"
